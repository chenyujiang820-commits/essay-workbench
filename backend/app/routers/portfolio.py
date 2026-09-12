"""学生维度的二期端点：作文成长档案（FR-06）。

* ``GET /api/students/{student_id}/portfolio`` 档案聚合（列表 + 统计）。

口径（PRD v1.3 §3 FR-06）：

* 只收**已定稿**稿件 —— 未定稿没有教育结论，列进档案会被家长误读成"这就是孩子写的"；
* "上榜次数"由佳作榜现算（``honour_pairs``），**不落新列**：榜单口径变了档案自动跟着变，
  避免出现两份会互相矛盾的真相；
* 星级同样由后端算，前端只渲染。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.models import Essay, Student
from app.ranking import honour_pairs, stars_from_score, to_scored_essays
from app.schemas import (
    ApiError,
    Envelope,
    PortfolioEntry,
    PortfolioOut,
    PortfolioStats,
    StudentOut,
    envelope,
)

router = APIRouter(prefix="/api/students", tags=["portfolio"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


@router.get("/{student_id}/portfolio", response_model=Envelope[PortfolioOut])
async def get_portfolio(
    student_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """单个学生的成长档案：按时间倒序的已定稿作文 + 统计。

    Raises:
        ApiError: 404 学生不存在。
    """
    settings: AppSettings = request.app.state.settings
    student = await session.get(Student, student_id)
    if student is None:
        raise ApiError("学生不存在", code=404, status_code=404)

    thresholds = settings.ranking_config()["thresholds"]
    stmt = (
        select(Essay)
        .where(Essay.student_id == student_id, Essay.status == "proofread")
        .options(
            selectinload(Essay.issue),
            selectinload(Essay.student),
            selectinload(Essay.photos),
        )
        .order_by(Essay.proofread_at.desc(), Essay.id.desc())
    )
    essays = list((await session.execute(stmt)).scalars().all())

    entries = [_to_entry(essay, thresholds) for essay in essays]
    honoured = await _honoured_count(session, student_id)
    scored = [float(essay.score) for essay in essays if essay.score is not None]
    stats = PortfolioStats(
        essay_count=len(entries),
        selected_count=sum(1 for essay in essays if essay.selected),
        honoured_count=honoured,
        top_stars=max((entry.stars for entry in entries), default=0),
        avg_score=round(sum(scored) / len(scored), 1) if scored else None,
        issue_count=len({essay.issue_id for essay in essays}),
        last_issue_no=max((essay.issue.issue_no for essay in essays if essay.issue), default=None),
    )
    payload = PortfolioOut(
        student=StudentOut.model_validate(student),
        stats=stats,
        entries=entries,
        class_name=settings.class_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return envelope(payload.model_dump())


def _to_entry(essay: Essay, thresholds: Any) -> PortfolioEntry:
    """作文 -> 档案条目（期号/星级/精选/评语一并带上，家长页与档案页共用这套字段）。"""
    issue = essay.issue
    return PortfolioEntry(
        essay_id=int(essay.id),
        issue_id=int(essay.issue_id),
        issue_no=int(issue.issue_no) if issue is not None else 0,
        week_start_date=str(issue.week_start_date) if issue is not None else "",
        title=(essay.title or "").strip(),
        score=essay.score,
        stars=stars_from_score(essay.score, thresholds),
        selected=int(essay.selected or 0),
        teacher_comment=essay.teacher_comment,
        proofread_at=essay.proofread_at,
        photo_count=len(list(essay.photos)),
    )


async def _honoured_count(session: AsyncSession, student_id: int) -> int:
    """该生"上榜"期数：佳作榜前 N 名（``HONOUR_RANKS``）落在他身上的次数。"""
    stmt = (
        select(Essay)
        .where(Essay.status == "proofread", Essay.score.is_not(None))
        .options(selectinload(Essay.student), selectinload(Essay.issue))
    )
    essays = list((await session.execute(stmt)).scalars().all())
    pairs = honour_pairs(to_scored_essays(essays))
    return sum(1 for honoured_student, _issue in pairs if honoured_student == student_id)