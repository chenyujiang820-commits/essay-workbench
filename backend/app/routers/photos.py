"""原片文件服务：``GET /api/photos/{photo_id}/file``（带鉴权）。

安全要点
* 鉴权：复用 ``require_auth``（``Authorization: Bearer <token>``）。
* 目录穿越防护：DB 中的 ``file_path`` 为**相对数据目录**的路径，解析为绝对路径后
  必须仍位于数据目录之内；越界一律 400（不泄露文件系统信息）。
* 响应带 ``Cache-Control: private, max-age=3600``（私有缓存，不落公共 CDN）。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import AppSettings
from app.db import get_session
from app.models import Photo
from app.schemas import ApiError

router = APIRouter(prefix="/api/photos", tags=["photos"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[str, Depends(require_auth)]

_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}


def resolve_photo_path(settings: AppSettings, file_path: str) -> Path:
    """把相对 ``file_path`` 解析为绝对路径，并强制其位于数据目录之内。

    Args:
        settings: 应用配置（提供数据目录）。
        file_path: DB 中记录的相对路径。

    Returns:
        数据目录内的绝对路径。

    Raises:
        ApiError: 400，路径越出数据目录（含目录穿越 / 绝对路径注入）。
    """
    data_root = settings.data_dir.resolve()
    candidate = (settings.data_dir / file_path).resolve()
    if not candidate.is_relative_to(data_root):
        raise ApiError("非法的照片路径", code=400, status_code=400)
    return candidate


@router.get("/{photo_id}/file")
async def get_photo_file(
    photo_id: int,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
) -> FileResponse:
    """按 photo_id 返回原片二进制流。

    Raises:
        ApiError: 401 未授权；404 照片记录或磁盘文件不存在；400 路径非法。
    """
    settings: AppSettings = request.app.state.settings

    photo = await session.get(Photo, photo_id)
    if photo is None:
        raise ApiError("照片不存在", code=404, status_code=404)

    target = resolve_photo_path(settings, photo.file_path)
    if not target.is_file():
        raise ApiError("照片文件缺失", code=404, status_code=404)

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(path=str(target), media_type=media_type, headers=_CACHE_HEADERS)
