"""T03：原片文件服务（鉴权 / 404 / 目录穿越 / Content-Type）。"""

from __future__ import annotations

from pathlib import Path

from app.models import Essay, Issue, Photo, Student, utcnow_iso
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"fake-jpeg-payload"


async def seed_photo(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    file_path: str,
    issue_no: int = 1,
) -> int:
    """插入 学生/期数/作文/照片，返回 photo_id。"""
    async with session_factory() as session:
        student = Student(student_no=f"S{issue_no:03d}", name="张三", created_at=utcnow_iso())
        issue = Issue(issue_no=issue_no, week_start_date="2026-09-07", created_at=utcnow_iso())
        session.add_all([student, issue])
        await session.flush()

        essay = Essay(issue_id=issue.id, student_id=student.id, created_at=utcnow_iso())
        session.add(essay)
        await session.flush()

        photo = Photo(
            essay_id=essay.id,
            seq=1,
            file_path=file_path,
            created_at=utcnow_iso(),
        )
        session.add(photo)
        await session.commit()
        await session.refresh(photo)
        return photo.id


async def test_photo_file_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/photos/1/file")
    assert response.status_code == 401
    assert response.json()["code"] == 401


async def test_photo_file_missing_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    photo_id = await seed_photo(session_factory, file_path="photos/1/1/absent.jpg")
    response = await client.get(f"/api/photos/{photo_id}/file", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["code"] == 404


async def test_photo_record_missing_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/api/photos/987654/file", headers=auth_headers)
    assert response.status_code == 404


async def test_photo_path_traversal_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    """越出数据目录的路径必须被拒绝（即使磁盘上该文件真实存在）。"""
    outside = data_dir.parent / "evil.jpg"
    outside.write_bytes(b"should-never-be-served")

    photo_id = await seed_photo(session_factory, file_path="../evil.jpg", issue_no=2)
    response = await client.get(f"/api/photos/{photo_id}/file", headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["code"] == 400


async def test_photo_absolute_path_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    """绝对路径注入到数据目录之外必须被拒绝（即便磁盘上该文件真实存在）。"""
    outside = data_dir.parent / "abs_evil.jpg"
    outside.write_bytes(b"should-never-be-served")

    photo_id = await seed_photo(session_factory, file_path=str(outside), issue_no=3)
    response = await client.get(f"/api/photos/{photo_id}/file", headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["code"] == 400


async def test_photo_absolute_path_inside_served(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    """绝对路径若落在数据目录之内，属合法路径，正常返回 200。"""
    relative = "photos/1/abs_inside.jpg"
    target = data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(JPEG_BYTES)

    photo_id = await seed_photo(session_factory, file_path=str(target.resolve()), issue_no=6)
    response = await client.get(f"/api/photos/{photo_id}/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")


async def test_photo_file_served_with_content_type(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    relative = "photos/1/demo.jpg"
    target = data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(JPEG_BYTES)

    photo_id = await seed_photo(session_factory, file_path=relative, issue_no=4)
    response = await client.get(f"/api/photos/{photo_id}/file", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.content == JPEG_BYTES


async def test_photo_file_png_content_type(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    relative = "photos/1/demo.png"
    target = data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    photo_id = await seed_photo(session_factory, file_path=relative, issue_no=5)
    response = await client.get(f"/api/photos/{photo_id}/file", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
