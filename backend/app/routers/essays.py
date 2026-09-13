"""作文路由：上传（多图）/ 列表 / 详情 / 校对保存。

* ``POST /api/issues/{issue_id}/essays``  多图 multipart 上传，受理后异步识别（202）。
* ``GET  /api/issues/{issue_id}/essays``  某期作文列表（状态看板用）。
* ``GET  /api/essays/{essay_id}``         详情（含识别结果与 diff）。
* ``PATCH /api/essays/{essay_id}``        校对保存：final_text 非空 + proofread=true 定稿。
* ``POST /api/essays/{essay_id}/recognize`` 重跑识别（FR-10：failed/review 稿重新排队）。
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.images import ensure_size, image_size, resolve_extension
from app.models import Essay, Issue, Photo, RecognitionTask, Student, utcnow_iso
from app.ranking import MAX_SELECTED_PER_ISSUE
from app.render.templates import natural_text_key
from app.schemas import (
    ApiError,
    Envelope,
    EssayDetail,
    EssayOut,
    EssayUpdate,
    RecognizeRerunOut,
    UploadResult,
    envelope,
    essay_to_detail,
    essay_to_out,
    task_to_out,
)

router = APIRouter(prefix="/api", tags=["essays"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]


async def _load_essay(session: AsyncSession, essay_id: int) -> Essay | None:
    """按 id 加载作文，并预加载照片/任务/学生。"""
    stmt = (
        select(Essay)
        .where(Essay.id == essay_id)
        .options(
            selectinload(Essay.photos),
            selectinload(Essay.tasks),
            selectinload(Essay.student),
        )
    )
    return (await session.execute(stmt)).scalars().first()


@router.post("/issues/{issue_id}/essays", status_code=202, response_model=Envelope[UploadResult])
async def upload_essay(
    request: Request,
    issue_id: int,
    student_id: Annotated[int, Form(description="学生 id")],
    files: Annotated[list[UploadFile], File(description="作文原片（多张）")],
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """受理一篇作文的多图上传，落盘原片并投递识别任务。

    Raises:
        ApiError: 404 期数/学生不存在；400 未提供有效照片 / 格式不在白名单 / 单张超过 15MB。
    """
    settings: AppSettings = request.app.state.settings

    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)

    student = await session.get(Student, student_id)
    if student is None:
        raise ApiError("学生不存在", code=404, status_code=404)

    valid_files = [upload for upload in files if upload.filename]
    if not valid_files:
        raise ApiError("请至少上传一张照片", code=400, status_code=400)

    prepared_files: list[tuple[bytes, str, tuple[int, int] | None]] = []
    for seq, upload in enumerate(valid_files, start=1):
        content = await upload.read()
        if not content:
            raise ApiError(f"第 {seq} 张照片内容为空", code=400, status_code=400)
        ensure_size(len(content))
        extension = resolve_extension(upload.filename, upload.content_type)
        dimensions = image_size(content)
        prepared_files.append((content, extension, dimensions))

    essay = Essay(
        issue_id=issue_id,
        student_id=student_id,
        title="",
        final_text="",
        status="uploaded",
        low_confidence=0,
        created_at=utcnow_iso(),
    )
    session.add(essay)
    await session.flush()  # 取得 essay.id

    issue_dir = settings.photos_dir / str(issue_id)
    issue_dir_existed = issue_dir.exists()
    target_dir = issue_dir / str(essay.id)
    written = False
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for seq, (content, extension, dimensions) in enumerate(prepared_files, start=1):
            filename = f"{uuid.uuid4().hex}{extension}"
            (target_dir / filename).write_bytes(content)
            written = True
            session.add(
                Photo(
                    essay_id=essay.id,
                    seq=seq,
                    file_path=f"photos/{issue_id}/{essay.id}/{filename}",
                    width=dimensions[0] if dimensions else None,
                    height=dimensions[1] if dimensions else None,
                    engine1_text=None,
                    engine2_text=None,
                    diff_json=None,
                    created_at=utcnow_iso(),
                )
            )
        task = RecognitionTask(
            essay_id=essay.id,
            step="queued",
            retry_count=0,
            error=None,
            updated_at=utcnow_iso(),
        )
        session.add(task)
        await session.commit()
    except Exception:
        await session.rollback()
        if written or target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if not issue_dir_existed and issue_dir.exists():
            with suppress(OSError):
                issue_dir.rmdir()
        raise
    await session.refresh(essay)
    await session.refresh(task)

    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        await worker.enqueue(essay.id)

    result = UploadResult(
        essay_id=essay.id,
        status=essay.status,
        photo_count=len(valid_files),
        task=task_to_out(task),
    )
    return envelope(result.model_dump(), message="已受理，正在识别")


@router.get("/issues/{issue_id}/essays", response_model=Envelope[list[EssayOut]])
async def list_issue_essays(
    issue_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """列出某期全部作文（按学号升序）。

    Raises:
        ApiError: 404 期数不存在。
    """
    settings: AppSettings = request.app.state.settings
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ApiError("期数不存在", code=404, status_code=404)

    stmt = (
        select(Essay)
        .where(Essay.issue_id == issue_id)
        .options(selectinload(Essay.student))
        .join(Student, Student.id == Essay.student_id)
        .order_by(Student.student_no.asc())
    )
    essays = sorted(
        (await session.execute(stmt)).scalars().all(),
        key=lambda essay: natural_text_key(essay.student.student_no if essay.student else ""),
    )
    thresholds = settings.ranking_config()["thresholds"]
    return envelope([essay_to_out(essay, thresholds) for essay in essays])


@router.get("/essays/{essay_id}", response_model=Envelope[EssayDetail])
async def get_essay(
    essay_id: int, request: Request, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """作文详情：含各 photo 的 engine1_text/engine2_text/diff_json 与状态。

    Raises:
        ApiError: 404 作文不存在。
    """
    essay = await _load_essay(session, essay_id)
    if essay is None:
        raise ApiError("作文不存在", code=404, status_code=404)
    thresholds = request.app.state.settings.ranking_config()["thresholds"]
    return envelope(essay_to_detail(essay, thresholds).model_dump())


@router.patch("/essays/{essay_id}", response_model=Envelope[EssayDetail])
async def update_essay(
    essay_id: int,
    payload: EssayUpdate,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, Any]:
    """保存校对结果；``proofread=true`` 且文字非空时定稿（写 proofread_at）。

    v1.3 起同一请求可一并提交评语 / 评分 / 精选（三者都是"不传即不改"）。它们**不参与
    状态机**：只写评语绝不会把 review 推成 proofread —— 这是二期最容易写错、后果最重的
    一条，用例单独守住（PRD v1.3 §10）。

    Raises:
        ApiError: 404 不存在；400 定稿文字为空 / 未定稿就评分或精选 / 本期精选已达上限。
    """
    settings: AppSettings = request.app.state.settings
    essay = await _load_essay(session, essay_id)
    if essay is None:
        raise ApiError("作文不存在", code=404, status_code=404)

    if payload.final_text is not None:
        if not payload.final_text.strip():
            raise ApiError("定稿文字不能为空", code=400, status_code=400)
        essay.final_text = payload.final_text

    if payload.proofread and not (essay.final_text or "").strip():
        # 定稿必须有正文：没传正文就用已存的那一份，两份都没有才拒绝。
        raise ApiError("定稿文字不能为空", code=400, status_code=400)
    # title 为 None 表示"本次不改标题"（Worker 自动抽取的结果得以保留）；传字符串则按老师意图覆盖。
    if payload.title is not None:
        essay.title = payload.title.strip()[:200]
    if payload.proofread:
        essay.status = "proofread"
        essay.proofread_at = utcnow_iso()

    # -- v1.3：评语 / 评分 / 精选。先判门禁再写入，避免"改了一半"。 --
    if payload.teacher_comment is not None:
        essay.teacher_comment = payload.teacher_comment or None
    # 三态判据落在"字段有没有出现"上，而不是值是不是 None：Pydantic 会把缺字段和
    # 显式 null 都折叠成 None，只看 ``payload.score`` 就永远分不清"这次不动分数"和
    # "把打错的分数取消" —— 后者是老师的正常操作。
    if "score" in payload.model_fields_set:
        if payload.score is None:
            essay.score = None
        elif essay.status != "proofread":
            raise ApiError("未定稿作文不可评分：请先完成校对定稿", code=400, status_code=400)
        else:
            essay.score = payload.score
    if payload.selected is not None:
        target = int(payload.selected)
        if target and essay.status != "proofread":
            raise ApiError("未定稿作文不可设为精选", code=400, status_code=400)
        if target and not int(essay.selected or 0):
            chosen = await _selected_in_issue(session, int(essay.issue_id), int(essay.id))
            if len(chosen) >= MAX_SELECTED_PER_ISSUE:
                raise ApiError(
                    f"本期精选已达上限 {MAX_SELECTED_PER_ISSUE} 篇，请先取消一篇再添加",
                    code=400,
                    status_code=400,
                )
        essay.selected = target

    await session.commit()

    refreshed = await _load_essay(session, essay_id)
    assert refreshed is not None  # 刚提交过，必然存在
    thresholds = settings.ranking_config()["thresholds"]
    return envelope(
        essay_to_detail(refreshed, thresholds).model_dump(), message="已保存"
    )


async def _selected_in_issue(
    session: AsyncSession, issue_id: int, except_essay_id: int
) -> list[int]:
    """本期已精选的其它作文 id（不含正在改的那一篇）。"""
    stmt = select(Essay.id).where(Essay.issue_id == issue_id, Essay.selected == 1)
    return [int(item) for item in (await session.execute(stmt)).scalars().all() if int(item) != except_essay_id]


@router.post("/essays/{essay_id}/recognize", status_code=202, response_model=Envelope[RecognizeRerunOut])
async def rerun_recognize(
    request: Request, essay_id: int, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """重新排队识别（FR-10：给识别失败/结果不满意的稿子一个出口）。

    语义：整篇重跑——清空各张的 engine1_text / engine2_text / diff_json（这三列
    "识别落库后只读"仅指正常流程内不增量改写，重跑是**整体重写**），把该篇最新任务
    置回 ``queued``、清除 ``error``、``retry_count += 1``，并唤醒 Worker。

    Raises:
        ApiError: 404 作文不存在；409 已定稿（不可覆盖老师成果）；400 该篇无原片。
    """
    essay = await _load_essay(session, essay_id)
    if essay is None:
        raise ApiError("作文不存在", code=404, status_code=404)
    if essay.status == "proofread":
        raise ApiError("该篇已定稿，不可覆盖", code=409, status_code=409)
    if not essay.photos:
        raise ApiError("该篇没有原片，无法识别", code=400, status_code=400)

    for photo in essay.photos:
        photo.engine1_text = None
        photo.engine2_text = None
        photo.diff_json = None

    task = _latest_task(essay)
    if task is None:
        task = RecognitionTask(essay_id=essay.id, step="queued", retry_count=0, updated_at=utcnow_iso())
        session.add(task)
    task.step = "queued"
    task.error = None
    task.retry_count = int(task.retry_count or 0) + 1
    task.updated_at = utcnow_iso()
    essay.status = "recognizing"
    await session.commit()

    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        await worker.enqueue(essay.id)

    result = RecognizeRerunOut(essay_id=essay.id, status=essay.status, task=task_to_out(task))
    return envelope(result.model_dump(), message="已重新排队识别")


def _latest_task(essay: Essay) -> RecognitionTask | None:
    """取该篇最新一条识别任务（一期约定一篇一任务；多条时按 id 最大）。"""
    if not essay.tasks:
        return None
    return max(essay.tasks, key=lambda task: task.id or 0)
