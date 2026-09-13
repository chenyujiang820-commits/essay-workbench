"""成册导出路由：模板清单 / 预览 / 整册 PDF / 单篇版式 PDF / 投屏数据。

* ``GET  /api/exports/templates``                       五套模板清单（key+中文名+描述）
* ``GET  /api/exports/{issue_id}/preview``              渲染整册 HTML（text/html，iframe 预览）
* ``GET  /api/exports/{issue_id}/present``              投屏数据（逐篇 name/title/paragraphs）
* ``POST /api/exports/{issue_id}``                      整册 PDF（校对铁律：未全定稿 409）
* ``POST /api/exports/{issue_id}/single/{essay_id}``    单篇版式 PDF（打印张贴用）

校对铁律：整册导出要求该期**全部定稿**（status == proofread），否则 409。
排序：``student_no``（默认）/ ``name`` / ``score`` / ``selected_score``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.models import Essay, Issue, Student
from app.render import pdf as pdf_render
from app.render import templates as tpl
from app.schemas import (
    ApiError,
    BookItemOut,
    Envelope,
    ExportRequest,
    PresentOut,
    TemplateInfo,
    envelope,
)

router = APIRouter(prefix="/api/exports", tags=["exports"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


async def _load_issue(session: AsyncSession, issue_id: int) -> Issue:
    """加载期数，不存在则 404。"""
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)
    return issue


async def _load_essays(session: AsyncSession, issue_id: int) -> list[Essay]:
    """加载某期全部作文（预加载学生，供渲染取名/排序）。"""
    stmt = (
        select(Essay)
        .where(Essay.issue_id == issue_id)
        .options(selectinload(Essay.student))
    )
    return list((await session.execute(stmt)).scalars().all())


def _build_meta(
    settings: AppSettings, issue: Issue, template: str, order: str
) -> dict[str, Any]:
    """构造渲染元数据（班级名来自 app.yaml，缺省回退默认值）。"""
    return tpl.build_meta(
        class_name=tpl.class_name_from_settings(settings),
        issue_no=issue.issue_no,
        week_start_date=issue.week_start_date,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        template=template,
        order=order,
    )


def _thresholds(settings: AppSettings) -> list[int]:
    """当前星级阈值（二期：模板要渲染星级，阈值只在后端算一次）。"""
    return list(settings.ranking_config()["thresholds"])


def _pdf_response(data: bytes, filename: str) -> Response:
    """构造 PDF 下载响应（ASCII 兜底 + RFC 5987 中文文件名）。"""
    disposition = (
        f"attachment; filename=\"essay-book.pdf\"; filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": disposition},
    )


@router.get("/templates", response_model=Envelope[list[TemplateInfo]])
async def list_templates(_auth: AuthDep) -> dict[str, Any]:
    """五套成册模板清单。"""
    return envelope([TemplateInfo(**item) for item in tpl.TEMPLATES])


@router.get("/{issue_id}/preview", response_class=HTMLResponse)
async def preview_book(
    issue_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
    template: Annotated[str, Query()] = tpl.DEFAULT_TEMPLATE,
    order: Annotated[str, Query()] = "student_no",
) -> HTMLResponse:
    """返回渲染后的整册 HTML（与 PDF 同一套模板，前端 iframe 预览）。

    Raises:
        ApiError: 400 该期暂无作文 / 参数非法；404 期数不存在。
    """
    settings: AppSettings = request.app.state.settings
    issue = await _load_issue(session, issue_id)
    essays = await _load_essays(session, issue_id)
    if not essays:
        raise ApiError("该期暂无作文，无法生成预览", code=400, status_code=400)

    template_key = tpl.validate_template(template)
    order_key = tpl.resolve_order(order)
    ordered = tpl.sort_essays(essays, order_key)
    html = tpl.render_book_html(
        ordered,
        template_key,
        _build_meta(settings, issue, template_key, order_key),
        settings=settings,
        thresholds=_thresholds(settings),
    )
    return HTMLResponse(content=html)


@router.get("/{issue_id}/present", response_model=Envelope[PresentOut])
async def present_data(
    issue_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
    order: Annotated[str, Query()] = "student_no",
) -> dict[str, Any]:
    """投屏数据：逐篇 name/title/paragraphs（供横版大字号讲评视图）。

    Raises:
        ApiError: 400 该期暂无作文 / 暂无可投屏文字；404 期数不存在。
    """
    settings: AppSettings = request.app.state.settings
    issue = await _load_issue(session, issue_id)
    essays = await _load_essays(session, issue_id)
    if not essays:
        raise ApiError("该期暂无作文，无法投屏", code=400, status_code=400)

    # 投屏列**全部有文字的**稿件（GAP-14）：上一轮只列 status=proofread，于是「拍完 3 篇、
    # 只定稿了 1 篇」时投屏里只剩那 1 篇，老师的原话是「只能看到一篇文章，另外一篇看不到」。
    # 未定稿的用识别初稿顶上并标 is_draft，让老师知道黑板上现在讲的是没定稿的稿子。
    bodies = [(essay, *tpl.present_body(essay)) for essay in essays]
    screenable = [(essay, text, draft) for essay, text, draft in bodies if text]
    excluded = len(essays) - len(screenable)
    if not screenable:
        raise ApiError(
            "该期暂无可投屏的作文（既无定稿文字也无识别文字），请先完成拍照识别或校对",
            code=400,
            status_code=400,
        )

    order_key = tpl.resolve_order(order)
    ordered = tpl.sort_essays([essay for essay, _, _ in screenable], order_key)
    draft_count = sum(1 for _, _, draft in screenable if draft)
    payload = PresentOut(
        class_name=tpl.class_name_from_settings(settings),
        issue_no=issue.issue_no,
        week_start_date=issue.week_start_date,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        items=[
            BookItemOut(**item)
            for item in tpl.build_items(ordered, include_draft_fallback=True)
        ],
        excluded_no_text=excluded,
        draft_count=draft_count,
    )
    return envelope(payload.model_dump())


@router.post("/{issue_id}")
async def create_export(
    issue_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
    payload: ExportRequest | None = None,
) -> Response:
    """导出整册 PDF（校对铁律：任一未定稿则 409）。

    请求体可省略（缺省 template=elegant / order=student_no）。

    Raises:
        ApiError: 400 该期暂无作文 / 参数非法；404 期数不存在；409 存在未定稿作文。
    """
    settings: AppSettings = request.app.state.settings
    issue = await _load_issue(session, issue_id)
    essays = await _load_essays(session, issue_id)
    if not essays:
        raise ApiError("该期暂无作文，无法成册", code=400, status_code=400)

    pending = [essay.id for essay in essays if essay.status != "proofread"]
    if pending:
        raise ApiError(
            f"存在 {len(pending)} 篇未定稿作文，未校对不可成册",
            code=409,
            status_code=409,
        )

    template_key = tpl.validate_template(payload.template if payload else None)
    order_key = tpl.resolve_order(payload.order if payload else None)
    ordered = tpl.sort_essays(essays, order_key)
    html = tpl.render_book_html(
        ordered,
        template_key,
        _build_meta(settings, issue, template_key, order_key),
        settings=settings,
        thresholds=_thresholds(settings),
    )
    data = await pdf_render.export_book(html)
    return _pdf_response(data, f"第{issue.issue_no}期作文集.pdf")


@router.post("/{issue_id}/single/{essay_id}")
async def create_single_export(
    issue_id: int,
    essay_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
    template: Annotated[str, Query()] = tpl.DEFAULT_TEMPLATE,
) -> Response:
    """导出单篇版式 PDF（打印张贴用）。该篇须已定稿。

    Raises:
        ApiError: 400 参数非法；404 期数/作文不存在；409 该篇未定稿。
    """
    settings: AppSettings = request.app.state.settings
    issue = await _load_issue(session, issue_id)
    essays = await _load_essays(session, issue_id)
    essay = next((item for item in essays if item.id == essay_id), None)
    if essay is None:
        raise ApiError("作文不存在", code=404, status_code=404)
    if essay.status != "proofread":
        raise ApiError("该篇尚未定稿，无法导出单篇版式", code=409, status_code=409)

    template_key = tpl.validate_template(template)
    name = essay.student.name if essay.student is not None else str(essay.id)
    # 兜底文案与 PRD FR-11 / 成册模板一致："未命名"（"无题"会被误读成学生真的没写题目）
    title = (essay.title or "未命名").strip() or "未命名"
    html = tpl.render_single_html(
        essay,
        template_key,
        _build_meta(settings, issue, template_key, "student_no"),
        settings=settings,
    )
    data = await pdf_render.export_single(html)
    return _pdf_response(data, f"第{issue.issue_no}期-{name}-{title}.pdf")


@router.post("/{issue_id}/poster")
async def create_poster(
    issue_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
) -> Response:
    """导出"本周精选"海报 PDF（A4 **单页**，直接发家长群，FR-05）。

    只出 ``selected=1`` 的稿件 —— 精选的前置条件是已定稿（由 selection 端点写入时守住），
    所以这里不再二次判定状态，但也不给未勾选的稿件留口子。

    Raises:
        ApiError: 400 该期还没有勾选精选作文；404 期数不存在。
    """
    settings: AppSettings = request.app.state.settings
    issue = await _load_issue(session, issue_id)
    essays = await _load_essays(session, issue_id)
    chosen = [essay for essay in essays if int(essay.selected or 0)]
    if not chosen:
        raise ApiError(
            "本期还没有勾选精选作文，请先在作文看板勾选（1~10 篇）",
            code=400,
            status_code=400,
        )

    ordered = tpl.sort_essays(chosen, "score")
    meta = tpl.build_meta(
        class_name=tpl.class_name_from_settings(settings),
        issue_no=issue.issue_no,
        week_start_date=issue.week_start_date,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        template="poster",
        order="score",
        book_title=f"第 {issue.issue_no} 期 · 本周精选",
        subtitle=f"{tpl.class_name_from_settings(settings)} · 共 {len(ordered)} 篇",
    )
    html = tpl.render_poster_html(
        ordered, meta, settings=settings, thresholds=_thresholds(settings)
    )
    data = await pdf_render.export_poster(html)
    return _pdf_response(data, f"第{issue.issue_no}期本周精选.pdf")


@router.post("/students/{student_id}/portfolio")
async def create_portfolio_export(
    student_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
    template: Annotated[str, Query()] = tpl.DEFAULT_TEMPLATE,
    order: Annotated[str, Query()] = "issue_no",
) -> Response:
    """导出单生"个人文集 PDF"（成长档案的期末交付物，FR-06）。

    复用五套成册模板，只换封面文案位；正文仍只取 ``final_text``（未定稿不进档案，
    自然也不会进文集）。

    Raises:
        ApiError: 400 该生暂无已定稿作文 / 参数非法；404 学生不存在。
    """
    settings: AppSettings = request.app.state.settings
    student = await session.get(Student, student_id)
    if student is None:
        raise ApiError("学生不存在", code=404, status_code=404)

    stmt = (
        select(Essay)
        .where(Essay.student_id == student_id, Essay.status == "proofread")
        .options(selectinload(Essay.student), selectinload(Essay.issue))
    )
    essays = list((await session.execute(stmt)).scalars().all())
    if not essays:
        raise ApiError(
            f"{student.name}还没有已定稿的作文，无法导出个人文集",
            code=400,
            status_code=400,
        )

    template_key = tpl.validate_template(template)
    # 档案默认按期号倒序（最近的写在最前面），也可切回学号/姓名/佳作序。
    ordered = _sort_for_portfolio(essays, order)
    newest = max((essay.issue.issue_no for essay in ordered if essay.issue), default=0)
    oldest = min((essay.issue.issue_no for essay in ordered if essay.issue), default=0)
    meta = tpl.build_meta(
        class_name=tpl.class_name_from_settings(settings),
        issue_no=newest,
        week_start_date=ordered[0].issue.week_start_date if ordered[0].issue else "",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        template=template_key,
        order=order,
        student_name=student.name,
        book_title=f"{student.name} 的作文成长档案",
        subtitle=f"{tpl.class_name_from_settings(settings)} · 第 {oldest}~{newest} 期",
    )
    html = tpl.render_portfolio_html(
        ordered,
        template_key,
        meta,
        settings=settings,
        thresholds=_thresholds(settings),
    )
    data = await pdf_render.export_book(html)
    return _pdf_response(data, f"{student.name}的作文成长档案.pdf")


def _sort_for_portfolio(essays: list[Essay], order: str) -> list[Essay]:
    """文集排序：``issue_no`` 走"期号倒序 + 学号升序"，其余交给成册同一套排序。

    刻意复用 ``tpl.sort_essays``：文集和整册的"学号/姓名/佳作序"必须是同一个实现，
    否则同一个班在两处看到不同的先后顺序，又是一次"两套口径"（GAP-14 的根因）。
    """
    if order != "issue_no":
        return tpl.sort_essays(essays, tpl.resolve_order(order))
    return sorted(
        essays,
        key=lambda essay: (
            -(int(essay.issue.issue_no) if essay.issue is not None else 0),
            essay.student.student_no if essay.student is not None else "",
        ),
    )
