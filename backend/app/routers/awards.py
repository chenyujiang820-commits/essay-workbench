"""期次维度的二期端点：精选勾选（FR-05）与三榜（FR-04）。

* ``PUT  /api/issues/{issue_id}/selection`` 整期覆盖式设置精选。
* ``GET  /api/issues/{issue_id}/ranking``   三榜（佳作/进步/星级）+ 其配置。

榜单的算法全部在 ``app/ranking.py``（纯函数），这里只做"取数 + 装配响应"，
这样口径改动永远只需要改一处，而取数改动不会影响判据的可测性。
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
from app.models import Essay, Issue
from app.ranking import (
    BOARD_KEYS,
    DEFAULT_SELECTED_SUGGEST,
    MAX_SELECTED_PER_ISSUE,
    ScoredEssay,
    progress_board,
    ranking_config,
    star_board,
    to_scored_essays,
    work_board,
)
from app.schemas import (
    ApiError,
    BoardConfigOut,
    Envelope,
    RankingOut,
    SelectionOut,
    SelectionUpdate,
    envelope,
)

router = APIRouter(prefix="/api", tags=["awards"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


async def _load_issue(session: AsyncSession, issue_id: int) -> Issue:
    """取期数，不存在 404。"""
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)
    return issue


async def _issue_essays(session: AsyncSession, issue_id: int) -> list[Essay]:
    """取某期全部作文（预加载学生/期数，供精选与榜单装配）。"""
    stmt = (
        select(Essay)
        .where(Essay.issue_id == issue_id)
        .options(selectinload(Essay.student), selectinload(Essay.issue))
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# FR-05 每周精选
# ---------------------------------------------------------------------------
@router.put("/issues/{issue_id}/selection", response_model=Envelope[SelectionOut])
async def set_selection(
    issue_id: int, payload: SelectionUpdate, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """整期覆盖式设置精选（PRD v1.3 Q28：一次提交整集，不逐篇开关）。

    校验顺序刻意是"先全部校验、后一次性写入"：任一不合法就整单不动数据库，
    老师不会遇到"勾到第 11 篇失败、结果前 10 篇的旧勾选也被清了"这种半套状态。

    Raises:
        ApiError: 404 期数不存在；400 有 id 不属于本期 / 有稿件未定稿 / 超过 10 篇。
    """
    await _load_issue(session, issue_id)
    essays = await _issue_essays(session, issue_id)
    by_id = {int(essay.id): essay for essay in essays}

    wanted = list(dict.fromkeys(int(item) for item in payload.essay_ids))  # 去重保序
    if len(wanted) > MAX_SELECTED_PER_ISSUE:
        raise ApiError(
            f"本期精选最多 {MAX_SELECTED_PER_ISSUE} 篇，当前提交了 {len(wanted)} 篇",
            code=400,
            status_code=400,
        )

    foreign = [item for item in wanted if item not in by_id]
    if foreign:
        raise ApiError(
            f"有 {len(foreign)} 篇作文不属于本期，无法设为精选",
            code=400,
            status_code=400,
        )

    drafts = [
        (by_id[item].student.name if by_id[item].student is not None else str(item))
        for item in wanted
        if by_id[item].status != "proofread"
    ]
    if drafts:
        raise ApiError(
            f"未定稿作文不可设为精选：{'、'.join(str(name) for name in drafts[:3])}",
            code=400,
            status_code=400,
        )

    chosen = set(wanted)
    for essay in essays:
        essay.selected = 1 if int(essay.id) in chosen else 0
    await session.commit()

    proofread = [essay for essay in essays if essay.status == "proofread"]
    result = SelectionOut(
        issue_id=issue_id,
        selected_ids=sorted(int(essay.id) for essay in essays if essay.selected),
        limit=MAX_SELECTED_PER_ISSUE,
        suggested=DEFAULT_SELECTED_SUGGEST,
        # 判据与 GET /ranking 完全一致：只数已定稿的，未定稿连"未评分"都不算。
        scored_count=len(_proofread_scored(essays)),
        unscored_count=sum(1 for essay in proofread if essay.score is None),
    )
    return envelope(result.model_dump(), message="精选已更新")


# ---------------------------------------------------------------------------
# FR-04 三榜
# ---------------------------------------------------------------------------
@router.get("/issues/{issue_id}/ranking", response_model=Envelope[RankingOut])
async def get_ranking(
    issue_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """该期三榜（佳作/进步/星级）与它们的开关、可见性。

    ``enabled: false`` 的榜返回空数组并把 key 记进 ``disabled``：界面要能分清
    "这周没人上榜"和"这个榜被关了"，两者对老师的含义完全不同。
    """
    settings: AppSettings = request.app.state.settings
    issue = await _load_issue(session, issue_id)
    config = ranking_config(settings.app_config())
    thresholds = config["thresholds"]

    essays = await _issue_essays(session, issue_id)
    current = _proofread_scored(essays)
    history = await _all_scored(session)

    boards = {
        "work": work_board(current, thresholds),
        "progress": progress_board(current, history, thresholds),
        "star": star_board(current, thresholds),
    }
    disabled = [key for key in BOARD_KEYS if not config["boards"][key]["enabled"]]
    for key in disabled:
        boards[key] = []

    proofread = [essay for essay in essays if essay.status == "proofread"]
    payload = RankingOut(
        issue_id=issue_id,
        issue_no=issue.issue_no,
        class_name=settings.class_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        thresholds=list(thresholds),
        config={key: BoardConfigOut(**value) for key, value in config["boards"].items()},
        work=boards["work"],  # type: ignore[arg-type]
        progress=boards["progress"],  # type: ignore[arg-type]
        star=boards["star"],  # type: ignore[arg-type]
        disabled=disabled,
        scored_count=len(current),
        unscored_count=sum(1 for essay in proofread if essay.score is None),
    )
    return envelope(payload.model_dump())


def _proofread_scored(essays: list[Essay]) -> list[ScoredEssay]:
    """已定稿且有评分的稿件 -> 榜单输入。

    未定稿不入榜：即使有人绕过接口直接往库里写了分，也不该出现在表彰里。
    """
    return to_scored_essays(
        [essay for essay in essays if essay.status == "proofread" and essay.score is not None]
    )


async def _all_scored(session: AsyncSession) -> list[ScoredEssay]:
    """全库"已定稿且有评分"的稿件 —— 进步榜靠它在所有更早期里找上期。

    规模是单班（45 人 × 20 期 = 900 行量级），一次取全表在 Python 层分组即可；
    二期不为不存在的规模加索引或缓存（PRD v1.3 §10）。
    """
    stmt = (
        select(Essay)
        .where(Essay.status == "proofread", Essay.score.is_not(None))
        .options(selectinload(Essay.student), selectinload(Essay.issue))
    )
    return to_scored_essays(list((await session.execute(stmt)).scalars().all()))