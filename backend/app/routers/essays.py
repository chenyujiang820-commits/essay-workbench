"""作文路由：上传（多图）/ 列表 / 详情 / 校对保存。

* ``POST /api/issues/{issue_id}/essays``  多图 multipart 上传，受理后异步识别（202）。
* ``GET  /api/issues/{issue_id}/essays``  某期作文列表（状态看板用）。
* ``GET  /api/essays/{essay_id}``         详情（含识别结果与 diff）。
* ``PATCH /api/essays/{essay_id}``        校对保存：final_text 非空 + proofread=true 定稿。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.models import Essay, Issue, Photo, RecognitionTask, Student, utcnow_iso
from app.schemas import (
    ApiError,
    Envelope,
    EssayDetail,
    EssayOut,
    EssayUpdate,
    UploadResult,
    envelope,
    essay_to_detail,
    essay_to_out,
    task_to_out,
)

router = APIRouter(prefix="/api", tags=["essays"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]

_MIME_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def _extension_for(content_type: str | None) -> str:
    """由 MIME 推断扩展名（缺省 .jpg，符合原片默认 jpg 约定）。"""
    if not content_type:
        return ".jpg"
    return _MIME_EXTENSION.get(content_type.lower(), ".jpg")


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
        ApiError: 404 期数/学生不存在；400 未提供有效照片。
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

    target_dir = settings.photos_dir / str(issue_id) / str(essay.id)
    target_dir.mkdir(parents=True, exist_ok=True)

    for seq, upload in enumerate(valid_files, start=1):
        content = await upload.read()
        if not content:
            raise ApiError(f"第 {seq} 张照片内容为空", code=400, status_code=400)
        filename = f"{uuid.uuid4().hex}{_extension_for(upload.content_type)}"
        (target_dir / filename).write_bytes(content)
        session.add(
            Photo(
                essay_id=essay.id,
                seq=seq,
                file_path=f"photos/{issue_id}/{essay.id}/{filename}",
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
    issue_id: int, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """列出某期全部作文（按学号升序）。

    Raises:
        ApiError: 404 期数不存在。
    """
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
    essays = (await session.execute(stmt)).scalars().all()
    return envelope([essay_to_out(essay) for essay in essays])


@router.get("/essays/{essay_id}", response_model=Envelope[EssayDetail])
async def get_essay(essay_id: int, session: SessionDep, _auth: AuthDep) -> dict[str, Any]:
    """作文详情：含各 photo 的 engine1_text/engine2_text/diff_json 与状态。

    Raises:
        ApiError: 404 作文不存在。
    """
    essay = await _load_essay(session, essay_id)
    if essay is None:
        raise ApiError("作文不存在", code=404, status_code=404)
    return envelope(essay_to_detail(essay).model_dump())


@router.patch("/essays/{essay_id}", response_model=Envelope[EssayDetail])
async def update_essay(
    essay_id: int, payload: EssayUpdate, session: SessionDep, _auth: AuthDep
) -> dict[str, Any]:
    """保存校对结果；``proofread=true`` 且文字非空时定稿（写 proofread_at）。

    Raises:
        ApiError: 404 不存在；400 定稿文字为空。
    """
    essay = await _load_essay(session, essay_id)
    if essay is None:
        raise ApiError("作文不存在", code=404, status_code=404)

    final_text = payload.final_text or ""
    if not final_text.strip():
        raise ApiError("定稿文字不能为空", code=400, status_code=400)

    essay.final_text = final_text
    if payload.proofread:
        essay.status = "proofread"
        essay.proofread_at = utcnow_iso()

    await session.commit()

    refreshed = await _load_essay(session, essay_id)
    assert refreshed is not None  # 刚提交过，必然存在
    return envelope(essay_to_detail(refreshed).model_dump(), message="已保存")
