"""期数 CRUD：/api/issues。"""

from __future__ import annotations

import shutil
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.models import Essay, Issue, utcnow_iso
from app.schemas import (
    ApiError,
    Envelope,
    IssueCreate,
    IssueOut,
    IssueUpdate,
    envelope,
    issue_to_out,
)

router = APIRouter(prefix="/api/issues", tags=["issues"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


async def _essay_counts(session: AsyncSession) -> dict[int, int]:
    """统计每期的作文数量。"""
    rows = await session.execute(select(Essay.issue_id, func.count(Essay.id)).group_by(Essay.issue_id))
    return {int(issue_id): int(count) for issue_id, count in rows.all()}


@router.get("", response_model=Envelope[list[IssueOut]])
async def list_issues(session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """列出全部期数（按期号倒序），附作文数量。"""
    issues = (await session.execute(select(Issue).order_by(Issue.issue_no.desc()))).scalars().all()
    counts = await _essay_counts(session)
    return envelope([issue_to_out(issue, counts.get(issue.id, 0)) for issue in issues])


@router.post("", status_code=201, response_model=Envelope[IssueOut])
async def create_issue(payload: IssueCreate, session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """新建一期。

    Raises:
        ApiError: 409 期号已存在。
    """
    existing = (
        await session.execute(select(Issue).where(Issue.issue_no == payload.issue_no))
    ).scalars().first()
    if existing is not None:
        raise ApiError(f"期号 {payload.issue_no} 已存在", code=409, status_code=409)

    issue = Issue(
        issue_no=payload.issue_no,
        week_start_date=payload.week_start_date,
        created_at=utcnow_iso(),
    )
    session.add(issue)
    await session.commit()
    await session.refresh(issue)
    return envelope(issue_to_out(issue, 0), message="期数已创建")


@router.get("/{issue_id}", response_model=Envelope[IssueOut])
async def get_issue(issue_id: int, session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """获取单期详情。

    Raises:
        ApiError: 404 期数不存在。
    """
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)
    counts = await _essay_counts(session)
    return envelope(issue_to_out(issue, counts.get(issue.id, 0)))


@router.patch("/{issue_id}", response_model=Envelope[IssueOut])
async def update_issue(
    issue_id: int, payload: IssueUpdate, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """更新期数（期号 / 周一日期）。

    Raises:
        ApiError: 404 期数不存在；409 期号冲突。
    """
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)

    if payload.issue_no is not None and payload.issue_no != issue.issue_no:
        clash = (
            await session.execute(select(Issue).where(Issue.issue_no == payload.issue_no))
        ).scalars().first()
        if clash is not None:
            raise ApiError(f"期号 {payload.issue_no} 已存在", code=409, status_code=409)
        issue.issue_no = payload.issue_no
    if payload.week_start_date is not None:
        issue.week_start_date = payload.week_start_date

    await session.commit()
    await session.refresh(issue)
    counts = await _essay_counts(session)
    return envelope(issue_to_out(issue, counts.get(issue.id, 0)), message="期数已更新")


@router.delete("/{issue_id}", response_model=Envelope[dict[str, Any]])
async def delete_issue(
    issue_id: int,
    request: Request,
    response: Response,
    session: SessionDep,
    _auth: AuthDep,
    confirm: bool = False,
) -> dict[str, Any]:
    """删除期数。

    * 期内无作文：直接删除。
    * 期内有作文：必须显式 ``confirm=true``（前端二次确认后传入），
      级联删除该期全部作文（含照片记录 / 识别任务），并清理原片目录。

    Raises:
        ApiError: 404 不存在；409 有作文但未携带 confirm。
    """
    settings: AppSettings = request.app.state.settings

    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)

    essay_ids = (
        await session.execute(select(Essay.id).where(Essay.issue_id == issue_id))
    ).scalars().all()

    if essay_ids and not confirm:
        raise ApiError(
            f"该期已有 {len(essay_ids)} 篇作文，删除将一并清除；请二次确认后重试",
            code=409,
            status_code=409,
        )

    await session.delete(issue)  # 级联删除 essays -> photos / tasks（ORM delete-orphan）
    await session.commit()

    # 原片目录按 issue 粒度整体清理。DB 已提交成功，磁盘清理失败不应把删除
    # 报成 500（否则用户会以为删除没生效）：残留目录下次删除同号期数前可手工清。
    photos_removed = True
    if essay_ids:
        photos_root = settings.photos_dir / str(issue_id)
        if photos_root.exists():
            try:
                shutil.rmtree(photos_root)
            except OSError:
                photos_removed = False

    return envelope(
        {"id": issue_id, "deleted_essays": len(essay_ids), "photos_removed": photos_removed},
        message="期数已删除" if photos_removed else "期数已删除（原片目录清理失败，可忽略或手动删除）",
    )
