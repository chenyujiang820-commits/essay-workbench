"""成册导出路由：POST /api/exports/{issue_id}。

一期「校对铁律」的流程级强制点：**该期存在任一未定稿（status != proofread）作文时，
拒绝成册并返回 409**。

说明：PDF 渲染（Jinja2 + Playwright）在 T04 实现；本接口当前完成前置校验并返回
就绪信息（含模板清单），T04 将在此基础上产出 PDF 字节。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.db import get_session
from app.models import Essay, Issue
from app.schemas import (
    DEFAULT_TEMPLATES,
    ApiError,
    Envelope,
    ExportReadiness,
    envelope,
)

router = APIRouter(prefix="/api/exports", tags=["exports"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


@router.post("/{issue_id}", response_model=Envelope[ExportReadiness])
async def create_export(issue_id: int, session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """校验该期是否满足成册条件（全部定稿）。

    Raises:
        ApiError: 404 期数不存在；409 无作文或存在未校对作文。
    """
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)

    essays = (
        await session.execute(select(Essay).where(Essay.issue_id == issue_id))
    ).scalars().all()
    if not essays:
        raise ApiError("该期暂无作文，无法成册", code=409, status_code=409)

    pending = [essay.id for essay in essays if essay.status != "proofread"]
    if pending:
        raise ApiError(
            f"存在 {len(pending)} 篇未定稿作文，未校对不可成册",
            code=409,
            status_code=409,
        )

    readiness = ExportReadiness(
        issue_id=issue_id,
        essay_count=len(essays),
        ready=True,
        templates=list(DEFAULT_TEMPLATES),
    )
    return envelope(readiness.model_dump(), message="成册条件满足")
