"""上传图片校验与元数据解析。

职责（与上传路由解耦，便于单测）：
* **格式白名单**：jpg / jpeg / png / webp / bmp（按 content-type 优先、扩展名兜底）。
* **大小上限**：单张 ≤ 15MB，超出抛 400（文案明确）。
* **像素尺寸**：用 Pillow 解析 ``(width, height)`` 落库；解析失败不阻断上传（尺寸留空）。

设计说明：本模块只做纯校验/解析，落盘与数据库写入仍在上传路由内完成。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.schemas import ApiError

#: 单张原片大小上限（15MB）。
MAX_PHOTO_BYTES: int = 15 * 1024 * 1024

#: 允许的落盘扩展名（小写，含点）。
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})

#: 允许的 MIME 类型。
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/x-ms-bmp",
    }
)

#: MIME -> 落盘扩展名（用于规范化，如 .jpeg 统一为 .jpg）。
_EXTENSION_BY_CONTENT_TYPE: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-ms-bmp": ".bmp",
}

#: 供错误文案复用的白名单提示。
ALLOWED_HINT: str = "jpg / jpeg / png / webp / bmp"


def resolve_extension(filename: str | None, content_type: str | None) -> str:
    """校验图片并返回落盘扩展名（content-type 优先，扩展名兜底）。

    Args:
        filename: 客户端文件名（可能为空）。
        content_type: 客户端 MIME（可能为空）。

    Returns:
        规范化的扩展名，如 ``.jpg`` / ``.png``。

    Raises:
        ApiError: 400，格式不在白名单。
    """
    ctype = (content_type or "").strip().lower()
    if ctype in _EXTENSION_BY_CONTENT_TYPE:
        return _EXTENSION_BY_CONTENT_TYPE[ctype]

    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix

    raise ApiError(f"仅支持 {ALLOWED_HINT} 格式的图片", code=400, status_code=400)


def ensure_size(size: int) -> None:
    """校验单张大小不超过上限。

    Args:
        size: 字节数。

    Raises:
        ApiError: 400，超出 15MB。
    """
    if size > MAX_PHOTO_BYTES:
        megabytes = size / 1024 / 1024
        raise ApiError(
            f"单张照片不能超过 15MB（当前约 {megabytes:.1f}MB）",
            code=400,
            status_code=400,
        )


def image_size(content: bytes) -> tuple[int, int] | None:
    """用 Pillow 解析图片像素尺寸。

    Args:
        content: 图片二进制内容。

    Returns:
        ``(width, height)``；内容非法/无法解析时返回 ``None``（调用方据此留空，不阻断上传）。
    """
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
        return int(width), int(height)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
