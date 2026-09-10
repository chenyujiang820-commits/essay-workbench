"""T05：上传加固测试（格式白名单 / 15MB 上限 / Pillow 尺寸落库）。

* ``resolve_extension`` / ``ensure_size`` / ``image_size`` 纯函数单测。
* 路由级：非白名单格式 400、超限 400、合法 PNG 尺寸落库、坏图不阻断上传。
"""

from __future__ import annotations

import io

import pytest
from app.images import (
    MAX_PHOTO_BYTES,
    ensure_size,
    image_size,
    resolve_extension,
)
from app.models import Student, utcnow_iso
from app.schemas import ApiError
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("a.jpg", "image/jpeg", ".jpg"),
        ("a.jpeg", "image/jpeg", ".jpg"),  # 规范化
        ("a.png", "image/png", ".png"),
        ("a.webp", "image/webp", ".webp"),
        ("a.bmp", "image/bmp", ".bmp"),
        ("noext", "image/jpeg", ".jpg"),  # content-type 优先
        ("a.JPG", None, ".jpg"),  # 扩展名兜底 + 大小写不敏感
        ("a.png", "application/octet-stream", ".png"),
    ],
)
def test_resolve_extension_accepts(filename: str, content_type: str | None, expected: str) -> None:
    assert resolve_extension(filename, content_type) == expected


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("a.gif", "image/gif"), ("a.txt", "text/plain"), ("a.pdf", "application/pdf"), (None, None)],
)
def test_resolve_extension_rejects(filename: str | None, content_type: str | None) -> None:
    with pytest.raises(ApiError) as excinfo:
        resolve_extension(filename, content_type)
    assert excinfo.value.status_code == 400
    assert "仅支持" in excinfo.value.message


def test_ensure_size_boundary() -> None:
    ensure_size(MAX_PHOTO_BYTES)  # 恰好 15MB：放行
    with pytest.raises(ApiError) as excinfo:
        ensure_size(MAX_PHOTO_BYTES + 1)
    assert excinfo.value.status_code == 400
    assert "15MB" in excinfo.value.message


def test_image_size_real_png_and_garbage() -> None:
    assert image_size(make_png(120, 80)) == (120, 80)
    assert image_size(b"not-an-image") is None


# ---------------------------------------------------------------------------
# 路由级
# ---------------------------------------------------------------------------
def make_png(width: int = 120, height: int = 80) -> bytes:
    """生成一张指定尺寸的白色 PNG。"""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


async def _seed_student(
    session_factory: async_sessionmaker[AsyncSession], student_no: str = "S001", name: str = "张三"
) -> int:
    async with session_factory() as session:
        student = Student(student_no=student_no, name=name, active=1, created_at=utcnow_iso())
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return student.id


async def _create_issue(client: AsyncClient, auth_headers: dict[str, str], issue_no: int = 1) -> int:
    response = await client.post(
        "/api/issues",
        json={"issue_no": issue_no, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["data"]["id"])


async def test_upload_rejects_unsupported_format(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student_id = await _seed_student(session_factory)
    issue_id = await _create_issue(client, auth_headers, issue_no=31)

    response = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("essay.gif", b"GIF89a", "image/gif"))],
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "仅支持" in response.json()["message"]


async def test_upload_rejects_oversize(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student_id = await _seed_student(session_factory)
    issue_id = await _create_issue(client, auth_headers, issue_no=32)

    oversize = b"\x00" * (MAX_PHOTO_BYTES + 1)
    response = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("big.jpg", oversize, "image/jpeg"))],
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "15MB" in response.json()["message"]


async def test_upload_persists_dimensions(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student_id = await _seed_student(session_factory)
    issue_id = await _create_issue(client, auth_headers, issue_no=33)

    upload = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("p.png", make_png(200, 150), "image/png"))],
        headers=auth_headers,
    )
    assert upload.status_code == 202, upload.text
    essay_id = upload.json()["data"]["essay_id"]

    detail = await client.get(f"/api/essays/{essay_id}", headers=auth_headers)
    photo = detail.json()["data"]["photos"][0]
    assert photo["width"] == 200
    assert photo["height"] == 150


async def test_upload_bad_image_does_not_block(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student_id = await _seed_student(session_factory)
    issue_id = await _create_issue(client, auth_headers, issue_no=34)

    upload = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("broken.jpg", b"not-really-an-image", "image/jpeg"))],
        headers=auth_headers,
    )
    assert upload.status_code == 202, upload.text
    essay_id = upload.json()["data"]["essay_id"]

    detail = await client.get(f"/api/essays/{essay_id}", headers=auth_headers)
    photo = detail.json()["data"]["photos"][0]
    assert photo["width"] is None
    assert photo["height"] is None
