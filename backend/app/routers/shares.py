"""家长查看（FR-07）：老师侧链接管理 + 公网只读通道。

两组路由：

* ``router``（需登录）：``POST /api/issues/{id}/shares``、``GET /api/shares``、
  ``DELETE /api/shares/{token}``；
* ``public_router``（**免鉴权**）：``GET /api/share/{token}`` 与 ``/api/share/{token}/preview``。

这是本项目**唯一的免鉴权数据面**，因此三条纪律写在这里：

1. 只读、只出**已定稿**正文 —— 分享链接不得成为绕过校对的第二条出口；
2. 无效令牌（不存在 / 已撤销 / 已过期）一律 410 同一文案，不做区分（防枚举）；
3. 响应 ``Cache-Control: no-store`` —— 家长可能用别人手机打开，不能把孩子的作文留在
   对方浏览器缓存里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.models import Essay, Issue, ShareLink, Student, utcnow_iso
from app.render import templates as tpl
from app.schemas import (
    ApiError,
    BookItemOut,
    Envelope,
    ShareCreate,
    ShareOut,
    ShareViewOut,
    envelope,
)
from app.share import (
    DEFAULT_SHARE_DAYS,
    MAX_SHARE_DAYS,
    MIN_SHARE_DAYS,
    SHARE_GONE_MESSAGE,
    expiry_iso,
    is_active,
    is_expired,
    new_share_token,
    share_path,
)

router = APIRouter(prefix="/api", tags=["shares"])
public_router = APIRouter(prefix="/api/share", tags=["share-public"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]

_NO_STORE = "no-store"


# ---------------------------------------------------------------------------
# 老师侧
# ---------------------------------------------------------------------------
@router.post("/issues/{issue_id}/shares", status_code=201, response_model=Envelope[ShareOut])
async def create_share(
    issue_id: int,
    session: SessionDep,
    _auth: AuthDep,
    payload: ShareCreate | None = None,
) -> dict[str, Any]:
    """为某期生成家长分享链接（可指定单生专属）。请求体可省略，缺省整期 + 14 天。

    Raises:
        ApiError: 404 期数/学生不存在；400 该期无已定稿作文 / 该生在本期无定稿作文。
    """
    body = payload or ShareCreate(days=DEFAULT_SHARE_DAYS)
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)

    student: Student | None = None
    if body.student_id is not None:
        student = await session.get(Student, body.student_id)
        if student is None:
            raise ApiError("学生不存在", code=404, status_code=404)

    essays = await _proofread_essays(session, issue_id, student_id=body.student_id)
    if not essays:
        if body.student_id is not None:
            raise ApiError(
                f"{'学生' if student is None else student.name}本期还没有已定稿作文，无法分享",
                code=400,
                status_code=400,
            )
        raise ApiError("该期还没有已定稿作文，请先完成校对再分享", code=400, status_code=400)

    link = ShareLink(
        token=new_share_token(),
        issue_id=issue_id,
        student_id=body.student_id,
        expires_at=expiry_iso(body.days),
        created_at=utcnow_iso(),
        revoked=0,
        label=body.label,
    )
    session.add(link)
    await session.commit()

    result = _to_share_out(link, issue.issue_no, student, len(essays))
    return envelope(result.model_dump(), message="分享链接已生成")


@router.get("/shares", response_model=Envelope[list[ShareOut]])
async def list_shares(
    session: SessionDep,
    _auth: AuthDep,
    issue_id: Annotated[int | None, Query(description="只看某期")] = None,
    include_expired: Annotated[bool, Query(description="是否包含已过期/已撤销")] = True,
) -> dict[str, Any]:
    """列出分享链接（含已撤销/已过期，便于老师看清"我到底发过哪几条"）。"""
    stmt = (
        select(ShareLink, Issue.issue_no, Student.name)
        .join(Issue, Issue.id == ShareLink.issue_id)
        .outerjoin(Student, Student.id == ShareLink.student_id)
        .order_by(ShareLink.created_at.desc())
    )
    if issue_id is not None:
        stmt = stmt.where(ShareLink.issue_id == issue_id)
    rows = (await session.execute(stmt)).all()

    # 每条链接显示"能看到几篇"：按"期 + 是否单生"现算，一次分组查完，不逐条发 SQL。
    counts = await _proofread_counts(session)
    items: list[ShareOut] = []
    for link, issue_no, student_name in rows:
        if not include_expired and not is_active(int(link.revoked or 0), str(link.expires_at)):
            continue
        student_id = int(link.student_id) if link.student_id is not None else None
        items.append(
            _to_share_out(
                link,
                int(issue_no),
                None,
                _visible_count(counts, int(link.issue_id), student_id),
                student_name=student_name,
            )
        )
    return envelope([item.model_dump() for item in items])


@router.delete("/shares/{token}", response_model=Envelope[dict[str, Any]])
async def revoke_share(
    token: str, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """撤销一条分享链接（置 ``revoked=1``，不删行）。

    不删行的理由：删了之后老师再点群里那条链接只会看到"链接不存在"，没法解释是自己
    撤的还是别人改的；留一行带撤销标记的记录才可诊断。

    Raises:
        ApiError: 404 链接不存在。
    """
    link = await session.get(ShareLink, token)
    if link is None:
        raise ApiError("分享链接不存在", code=404, status_code=404)
    link.revoked = 1
    await session.commit()
    return envelope({"token_hidden": True, "revoked": 1}, message="链接已撤销")


# ---------------------------------------------------------------------------
# 公网只读（免鉴权）
# ---------------------------------------------------------------------------
@public_router.get("/{token}", response_model=Envelope[ShareViewOut])
async def share_view(
    token: str,
    request: Request,
    session: SessionDep,
    response: Response,
) -> dict[str, Any]:
    """家长视图 JSON：整期或单生的**已定稿**作文（含评语与星级）。

    Raises:
        ApiError: 410 令牌无效（不存在 / 已撤销 / 已过期，三者故意同一文案）。
    """
    settings: AppSettings = request.app.state.settings
    link = await _active_link(session, token)
    response.headers["Cache-Control"] = _NO_STORE
    issue, essays, student = await _share_payload_essays(session, link)
    thresholds = settings.ranking_config()["thresholds"]
    items = [
        BookItemOut(**_minimize_for_parent(item))
        for item in tpl.build_items(essays, thresholds=thresholds)
    ]
    payload = ShareViewOut(
        class_name=settings.class_name,
        issue_no=issue.issue_no,
        week_start_date=issue.week_start_date,
        generated_at=_now_text(),
        expires_at=str(link.expires_at),
        scope="student" if student is not None else "issue",
        student_name=student.name if student is not None else None,
        items=items,
    )
    return envelope(payload.model_dump())


@public_router.get("/{token}/preview", response_class=HTMLResponse)
async def share_preview(
    token: str,
    request: Request,
    session: SessionDep,
    template: Annotated[str, Query(description="成册模板 key")] = tpl.DEFAULT_TEMPLATE,
) -> HTMLResponse:
    """家长只读预览 HTML：与成册**同一套模板**（家长看到的就是 PDF 的版式）。

    这条路径**没有**登录态：整册预览的 HTML 要能被家长直接打开，所以它挂在
    ``/api/share/*`` 这个免鉴权前缀下，并靠 ``no-store`` + 只出已定稿内容兜住风险。

    Raises:
        ApiError: 410 令牌无效；400 模板不存在。
    """
    settings: AppSettings = request.app.state.settings
    link = await _active_link(session, token)
    issue, essays, student = await _share_payload_essays(session, link)
    meta = tpl.build_meta(
        class_name=settings.class_name,
        issue_no=issue.issue_no,
        week_start_date=issue.week_start_date,
        generated_at=_now_text(),
        template=template,
        order="student_no",
        student_name=student.name if student is not None else "",
        book_title=(
            f"{student.name} 的作文" if student is not None else f"第 {issue.issue_no} 期作文集"
        ),
        subtitle=f"{settings.class_name} · 周一起 {issue.week_start_date}",
        hide_student_no=True,
    )
    # 家长页要显示"本链接有效期至"：时间只在 ShareLink 行上，随 meta 透给模板。
    meta["expires_at"] = str(link.expires_at)
    html = tpl.render_share_html(
        essays,
        template,
        meta,
        settings=settings,
        thresholds=settings.ranking_config()["thresholds"],
    )
    # 头必须挂在**返回的那个响应**上：本函数返回的是新建的 HTMLResponse，
    # 往注入的 ``response`` 上写会被 FastAPI 忽略（JSON 路由返回 dict 时才生效）。
    return HTMLResponse(content=html, headers={"Cache-Control": _NO_STORE})


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _now_text() -> str:
    """展示用生成时刻（与成册/投屏同一格式，不给家长看到一个不一样的时间样式）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


async def _proofread_counts(session: AsyncSession) -> dict[tuple[int, int | None], int]:
    """各期（及各期各生）的已定稿篇数：``(issue_id, student_id)`` -> 篇数。

    ``student_id`` 为 ``None`` 的键表示"整期可见篇数"。
    """
    stmt = (
        select(Essay.issue_id, Essay.student_id, func.count(Essay.id))
        .where(Essay.status == "proofread")
        .group_by(Essay.issue_id, Essay.student_id)
    )
    per_student: dict[tuple[int, int], int] = {}
    per_issue: dict[int, int] = {}
    for row in (await session.execute(stmt)).all():
        issue_id, student_id, count = int(row[0]), int(row[1]), int(row[2])
        per_student[(issue_id, student_id)] = count
        per_issue[issue_id] = per_issue.get(issue_id, 0) + count
    counts: dict[tuple[int, int | None], int] = {
        key: value for key, value in per_student.items()
    }
    for issue_id, total in per_issue.items():
        counts[(issue_id, None)] = total
    return counts


def _visible_count(
    counts: dict[tuple[int, int | None], int], issue_id: int, student_id: int | None
) -> int:
    """链接可见篇数：单生链接看该生，整期链接看全期。"""
    return counts.get((issue_id, student_id), 0)


async def _active_link(session: AsyncSession, token: str) -> ShareLink:
    """取仍然有效的链接；无效一律 410 且同一文案。"""
    link = await session.get(ShareLink, token)
    if link is None:
        raise ApiError(SHARE_GONE_MESSAGE, code=410, status_code=410)
    if int(link.revoked or 0) or is_expired(str(link.expires_at)):
        raise ApiError(SHARE_GONE_MESSAGE, code=410, status_code=410)
    return link


async def _share_payload_essays(
    session: AsyncSession, link: ShareLink
) -> tuple[Issue, list[Essay], Student | None]:
    """链接 -> (期数, 可见稿件, 单生学生或 None)。"""
    issue = await session.get(Issue, int(link.issue_id))
    if issue is None:  # pragma: no cover - 外键 CASCADE 正常时不会发生
        raise ApiError(SHARE_GONE_MESSAGE, code=410, status_code=410)
    student_id = int(link.student_id) if link.student_id is not None else None
    student = await session.get(Student, student_id) if student_id is not None else None
    essays = await _proofread_essays(session, int(link.issue_id), student_id=student_id)
    if not essays:  # 建链之后全部被删/退回未定稿：按失效处理，不给家长一个空白页
        raise ApiError(SHARE_GONE_MESSAGE, code=410, status_code=410)
    return issue, essays, student


async def _proofread_essays(
    session: AsyncSession, issue_id: int, *, student_id: int | None
) -> list[Essay]:
    """某期（可限某生）**已定稿**作文，按学号升序 —— 与成册默认序一致。"""
    stmt = (
        select(Essay)
        .where(Essay.issue_id == issue_id, Essay.status == "proofread")
        .options(selectinload(Essay.student), selectinload(Essay.photos))
        .join(Student, Student.id == Essay.student_id)
        .order_by(Student.student_no.asc())
    )
    if student_id is not None:
        stmt = stmt.where(Essay.student_id == student_id)
    return list((await session.execute(stmt)).scalars().all())


def _minimize_for_parent(item: dict[str, Any]) -> dict[str, Any]:
    """家长视图的数据最小化：抹掉学号与分数。

    学号是校内编排、分数是校内评价口径，家长页与 PDF 都只出**星级**；
    前端虽然不渲染这两个字段，但免鉴权接口把它们发出去就是白送给任何拿到链接的人，
    所以在这里一次性不发（PRD v1.3 §9 回归锁）。
    """
    cleaned = dict(item)
    cleaned["student_no"] = ""
    cleaned["score"] = None
    return cleaned


def _to_share_out(
    link: ShareLink,
    issue_no: int,
    student: Student | None,
    essay_count: int,
    *,
    student_name: str | None = None,
) -> ShareOut:
    """ORM -> 分享链接响应模型。"""
    token = str(link.token)
    return ShareOut(
        token=token,
        url=share_path(token),
        issue_id=int(link.issue_id),
        issue_no=issue_no,
        student_id=int(link.student_id) if link.student_id is not None else None,
        student_name=(student.name if student is not None else student_name),
        scope="student" if link.student_id is not None else "issue",
        created_at=str(link.created_at),
        expires_at=str(link.expires_at),
        revoked=int(link.revoked or 0),
        label=str(link.label or ""),
        essay_count=essay_count,
    )


__all__ = [
    "MAX_SHARE_DAYS",
    "MIN_SHARE_DAYS",
    "public_router",
    "router",
]