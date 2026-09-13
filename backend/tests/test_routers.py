"""T02：路由契约测试（401 / 上传 202 / 详情 / PATCH 定稿 / 未校对不可成册 409）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.models import Student, utcnow_iso
from app.schemas import ApiError
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def create_student(
    session_factory: async_sessionmaker[AsyncSession], student_no: str = "S001", name: str = "张三"
) -> int:
    async with session_factory() as session:
        student = Student(student_no=student_no, name=name, active=1, created_at=utcnow_iso())
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return student.id


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
async def test_login_success(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"password": "test-pass-123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["token"]
    assert payload["data"]["token_type"] == "bearer"
    assert payload["data"]["expires_in"] == 3600


async def test_login_wrong_password(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json() == {"code": 401, "data": None, "message": "口令错误"}


async def test_protected_endpoint_requires_token(client: AsyncClient) -> None:
    response = await client.get("/api/issues")
    assert response.status_code == 401
    assert response.json()["code"] == 401

    bad = await client.get("/api/issues", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401


async def test_health_is_public(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


# ---------------------------------------------------------------------------
# 期数 CRUD
# ---------------------------------------------------------------------------
async def test_issue_crud(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post(
        "/api/issues",
        json={"issue_no": 1, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    issue_id = created.json()["data"]["id"]

    duplicate = await client.post(
        "/api/issues",
        json={"issue_no": 1, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    assert duplicate.status_code == 409

    listed = await client.get("/api/issues", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1
    assert listed.json()["data"][0]["essay_count"] == 0

    detail = await client.get(f"/api/issues/{issue_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["issue_no"] == 1

    updated = await client.patch(
        f"/api/issues/{issue_id}",
        json={"week_start_date": "2026-09-14"},
        headers=auth_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["week_start_date"] == "2026-09-14"

    deleted = await client.delete(f"/api/issues/{issue_id}", headers=auth_headers)
    assert deleted.status_code == 200

    missing = await client.get(f"/api/issues/{issue_id}", headers=auth_headers)
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# 上传 -> 详情 -> 校对
# ---------------------------------------------------------------------------
async def test_upload_detail_and_proofread(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    student_id = await create_student(session_factory)
    issue_resp = await client.post(
        "/api/issues",
        json={"issue_no": 1, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    issue_id = issue_resp.json()["data"]["id"]

    files = [
        ("files", ("a.jpg", b"fake-image-one", "image/jpeg")),
        ("files", ("b.jpg", b"fake-image-two", "image/jpeg")),
    ]
    upload = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=files,
        headers=auth_headers,
    )
    assert upload.status_code == 202, upload.text
    body = upload.json()
    assert body["code"] == 0
    assert body["data"]["photo_count"] == 2
    assert body["data"]["status"] == "uploaded"
    assert body["data"]["task"]["step"] == "queued"
    essay_id = body["data"]["essay_id"]

    # 原片落盘
    essay_dir = data_dir / "photos" / str(issue_id) / str(essay_id)
    assert essay_dir.is_dir()
    assert len(list(essay_dir.iterdir())) == 2

    detail = await client.get(f"/api/essays/{essay_id}", headers=auth_headers)
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["status"] == "uploaded"
    assert data["low_confidence"] == 0
    assert data["student_name"] == "张三"
    assert len(data["photos"]) == 2
    assert data["photos"][0]["seq"] == 1
    assert data["photos"][0]["engine1_text"] is None
    assert data["photos"][0]["diff_json"] is None
    assert data["task"]["step"] == "queued"

    # 空定稿文字 -> 400
    empty = await client.patch(
        f"/api/essays/{essay_id}", json={"final_text": "   ", "proofread": True}, headers=auth_headers
    )
    assert empty.status_code == 400

    # 未校对时不可成册 -> 409
    blocked = await client.post(f"/api/exports/{issue_id}", headers=auth_headers)
    assert blocked.status_code == 409
    assert "未校对" in blocked.json()["message"]

    # 定稿
    saved = await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": "春天的校园里，玉兰花开了。", "proofread": True},
        headers=auth_headers,
    )
    assert saved.status_code == 200
    saved_data = saved.json()["data"]
    assert saved_data["status"] == "proofread"
    assert saved_data["proofread_at"] is not None
    assert saved_data["final_text"] == "春天的校园里，玉兰花开了。"

    # 全部定稿后可成册（T04：导出为整册 PDF 字节流）
    ready = await client.post(f"/api/exports/{issue_id}", headers=auth_headers)
    assert ready.status_code == 200
    assert ready.headers["content-type"] == "application/pdf"
    assert "attachment" in ready.headers["content-disposition"]
    assert ready.content[:5] == b"%PDF-"


async def test_upload_requires_student_and_files(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    issue_resp = await client.post(
        "/api/issues",
        json={"issue_no": 2, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    issue_id = issue_resp.json()["data"]["id"]

    no_student = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": "999"},
        files=[("files", ("a.jpg", b"data", "image/jpeg"))],
        headers=auth_headers,
    )
    assert no_student.status_code == 404

    missing_issue = await client.post(
        "/api/issues/99999/essays",
        data={"student_id": "1"},
        files=[("files", ("a.jpg", b"data", "image/jpeg"))],
        headers=auth_headers,
    )
    assert missing_issue.status_code == 404


async def test_list_issue_essays(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student_id = await create_student(session_factory, student_no="S002", name="李四")
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 3, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]

    await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("a.jpg", b"data", "image/jpeg"))],
        headers=auth_headers,
    )

    listed = await client.get(f"/api/issues/{issue_id}/essays", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1
    assert listed.json()["data"][0]["student_name"] == "李四"


async def test_export_missing_issue_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/api/exports/424242", headers=auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 边界与错误路径
# ---------------------------------------------------------------------------
async def test_login_payload_validation(client: AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert body["data"] is None


async def test_issue_update_conflict_returns_409(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    first = (
        await client.post(
            "/api/issues",
            json={"issue_no": 11, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]
    await client.post(
        "/api/issues",
        json={"issue_no": 12, "week_start_date": "2026-09-07"},
        headers=auth_headers,
    )
    clash = await client.patch(
        f"/api/issues/{first}", json={"issue_no": 12}, headers=auth_headers
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == 409

    ok = await client.patch(
        f"/api/issues/{first}", json={"issue_no": 13}, headers=auth_headers
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["issue_no"] == 13


async def test_issue_rejects_invalid_iso_date(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/issues",
        json={"issue_no": 14, "week_start_date": "2026-02-30"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 422


async def test_issue_update_rejects_invalid_iso_date(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 15, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]
    response = await client.patch(
        f"/api/issues/{issue_id}",
        json={"week_start_date": "2026-9-7"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 422


async def test_delete_issue_with_essays_requires_confirm_then_cascades(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    """删除有作文的期数：未带 confirm 409 拒绝；confirm=true 级联删除并清理照片目录。"""
    student_id = await create_student(session_factory, student_no="S009", name="王五")
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 21, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]
    upload = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("a.jpg", b"data", "image/jpeg"))],
        headers=auth_headers,
    )
    essay_id = upload.json()["data"]["essay_id"]

    # 未确认：409 拒绝，数据保留
    blocked = await client.delete(f"/api/issues/{issue_id}", headers=auth_headers)
    assert blocked.status_code == 409
    assert (await client.get(f"/api/essays/{essay_id}", headers=auth_headers)).status_code == 200

    # 带确认：级联删除作文，原片目录一并清理
    ok = await client.delete(
        f"/api/issues/{issue_id}", params={"confirm": "true"}, headers=auth_headers
    )
    assert ok.status_code == 200
    assert (await client.get(f"/api/essays/{essay_id}", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/api/issues/{issue_id}", headers=auth_headers)).status_code == 404
    photos_root = data_dir / "photos" / str(issue_id)
    assert not photos_root.exists() or not any(photos_root.iterdir())


async def test_delete_issue_photo_cleanup_failure_still_200(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 级联已提交后磁盘清理失败：不报 500，返回 photos_removed=false。"""
    student_id = await create_student(session_factory, student_no="S009b", name="王五二")
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 22, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]
    await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("a.jpg", b"data", "image/jpeg"))],
        headers=auth_headers,
    )

    def _boom(path: object, *args: object, **kwargs: object) -> None:
        raise OSError("disk guard blocked")

    monkeypatch.setattr("app.routers.issues.shutil.rmtree", _boom)
    ok = await client.delete(
        f"/api/issues/{issue_id}", params={"confirm": "true"}, headers=auth_headers
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["data"]["photos_removed"] is False
    assert "手动删除" in body["message"]
    # DB 侧级联已生效
    assert (await client.get(f"/api/issues/{issue_id}", headers=auth_headers)).status_code == 404


async def test_upload_empty_file_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    student_id = await create_student(session_factory, student_no="S010", name="赵六")
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 31, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]

    empty = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("a.jpg", b"", "image/jpeg"))],
        headers=auth_headers,
    )
    assert empty.status_code == 400

    no_files = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        headers=auth_headers,
    )
    assert no_files.status_code == 422  # multipart 缺 files 字段


async def test_upload_invalid_later_file_leaves_no_orphan(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
) -> None:
    student_id = await create_student(session_factory, student_no="S011", name="周七")
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 35, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]

    response = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[
            ("files", ("ok.jpg", b"first", "image/jpeg")),
            ("files", ("bad.gif", b"second", "image/gif")),
        ],
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert (
        await client.get(f"/api/issues/{issue_id}/essays", headers=auth_headers)
    ).json()["data"] == []
    photos_root = data_dir / "photos" / str(issue_id)
    assert not photos_root.exists() or not any(photos_root.iterdir())


async def test_upload_commit_failure_cleans_written_files(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    student_id = await create_student(session_factory, student_no="S012", name="郑八")
    issue_id = (
        await client.post(
            "/api/issues",
            json={"issue_no": 36, "week_start_date": "2026-09-07"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]

    async def _boom(_self: AsyncSession) -> None:
        raise ApiError("commit failed", code=500, status_code=500)

    monkeypatch.setattr(AsyncSession, "commit", _boom)
    response = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("ok.jpg", b"first", "image/jpeg"))],
        headers=auth_headers,
    )
    assert response.status_code == 500
    assert (
        await client.get(f"/api/issues/{issue_id}/essays", headers=auth_headers)
    ).json()["data"] == []
    photos_root = data_dir / "photos" / str(issue_id)
    assert not photos_root.exists() or not any(photos_root.iterdir())


async def test_essay_and_issue_lookup_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert (await client.get("/api/essays/987654", headers=auth_headers)).status_code == 404
    assert (
        await client.get("/api/issues/987654/essays", headers=auth_headers)
    ).status_code == 404
    assert (
        await client.patch(
            "/api/essays/987654", json={"final_text": "x", "proofread": True}, headers=auth_headers
        )
    ).status_code == 404
    assert (
        await client.patch("/api/issues/987654", json={"issue_no": 5}, headers=auth_headers)
    ).status_code == 404
    assert (await client.delete("/api/issues/987654", headers=auth_headers)).status_code == 404


# ---------------------------------------------------------------------------
# 学生名单（只读）
# ---------------------------------------------------------------------------
async def test_students_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/students")
    assert response.status_code == 401


async def test_students_list_sorted_by_student_no(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_student(session_factory, student_no="S002", name="李四")
    await create_student(session_factory, student_no="S001", name="张三")

    response = await client.get("/api/students", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert [item["student_no"] for item in payload["data"]] == ["S001", "S002"]
    assert payload["data"][0]["name"] == "张三"
    assert payload["data"][0]["active"] == 1


async def test_students_excludes_inactive(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Student(student_no="S900", name="已离校", active=0, created_at=utcnow_iso())
        )
        await session.commit()

    response = await client.get("/api/students", headers=auth_headers)
    assert response.json()["data"] == []


# ---------------------------------------------------------------------------
# 学生管理（新增 / 改名 / 停用 / 批量导入）
# ---------------------------------------------------------------------------
async def test_create_student_success_and_duplicate_conflict(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    ok = await client.post(
        "/api/students",
        json={"student_no": "S101", "name": "王小明"},
        headers=auth_headers,
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["data"]["student_no"] == "S101"
    assert body["data"]["name"] == "王小明"
    assert body["data"]["active"] == 1

    dup = await client.post(
        "/api/students",
        json={"student_no": "S101", "name": "重复学号"},
        headers=auth_headers,
    )
    assert dup.status_code == 409


async def test_student_fields_are_trimmed(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/students",
        json={"student_no": "  S104  ", "name": "  王小明  "},
        headers=auth_headers,
    )
    assert created.status_code == 201
    assert created.json()["data"]["student_no"] == "S104"
    assert created.json()["data"]["name"] == "王小明"

    blank = await client.post(
        "/api/students",
        json={"student_no": "   ", "name": "有效姓名"},
        headers=auth_headers,
    )
    assert blank.status_code == 422


async def test_student_update_and_import_trim_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    created = await client.post(
        "/api/students",
        json={"student_no": "S105", "name": "旧姓名"},
        headers=auth_headers,
    )
    student_id = created.json()["data"]["id"]

    patched = await client.patch(
        f"/api/students/{student_id}",
        json={"student_no": "  S105A  ", "name": "  新 姓名  "},
        headers=auth_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["student_no"] == "S105A"
    assert patched.json()["data"]["name"] == "新 姓名"

    imported = await client.post(
        "/api/students/import",
        json={
            "students": [
                {"student_no": "  S105A ", "name": "  导入 姓名 "},
                {"student_no": " S106 ", "name": " 新学生 "},
            ]
        },
        headers=auth_headers,
    )
    assert imported.status_code == 200
    listing = (await client.get("/api/students", headers=auth_headers)).json()["data"]
    by_no = {item["student_no"]: item["name"] for item in listing}
    assert by_no["S105A"] == "导入 姓名"
    assert by_no["S106"] == "新学生"

    blank_import = await client.post(
        "/api/students/import",
        json={"students": [{"student_no": "   ", "name": "有效姓名"}]},
        headers=auth_headers,
    )
    assert blank_import.status_code == 422


async def test_update_student_name(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    created = (
        await client.post(
            "/api/students",
            json={"student_no": "S102", "name": "旧名"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]

    patched = await client.patch(
        f"/api/students/{created}", json={"name": "新名"}, headers=auth_headers
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "新名"
    assert patched.json()["data"]["student_no"] == "S102"


async def test_deactivate_student_hides_from_list(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    created = (
        await client.post(
            "/api/students",
            json={"student_no": "S103", "name": "转学生"},
            headers=auth_headers,
        )
    ).json()["data"]["id"]

    removed = await client.delete(f"/api/students/{created}", headers=auth_headers)
    assert removed.status_code == 200

    # 名单中不再出现（active=0，非物理删除，保留历史作文关联）
    listing = await client.get("/api/students", headers=auth_headers)
    nos = [item["student_no"] for item in listing.json()["data"]]
    assert "S103" not in nos


async def test_import_students_batch(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """批量导入：新增+更新混合，重复学号视为改名更新。"""
    result = await client.post(
        "/api/students/import",
        json={"students": [
            {"student_no": "S201", "name": "李雷"},
            {"student_no": "S202", "name": "韩梅梅"},
        ]},
        headers=auth_headers,
    )
    assert result.status_code == 200
    body = result.json()["data"]
    assert body["created"] == 2
    assert body["updated"] == 0

    # 再次导入：S201 改名（updated），新增 S203
    again = await client.post(
        "/api/students/import",
        json={"students": [
            {"student_no": "S201", "name": "李雷雷"},
            {"student_no": "S203", "name": "林涛"},
        ]},
        headers=auth_headers,
    )
    body2 = again.json()["data"]
    assert body2["created"] == 1
    assert body2["updated"] == 1

    listing = (await client.get("/api/students", headers=auth_headers)).json()["data"]
    by_no = {item["student_no"]: item["name"] for item in listing}
    assert by_no["S201"] == "李雷雷"
    assert by_no["S202"] == "韩梅梅"
    assert by_no["S203"] == "林涛"


async def test_student_file_import_previews_csv_without_writing(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/students/import-file",
        files={"file": ("roster.csv", "学生学号,学生姓名\nS301,赵六\nS302,钱七\n", "text/csv")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid_count"] == 2
    assert data["invalid_count"] == 0
    assert [row["student_no"] for row in data["rows"]] == ["S301", "S302"]
    assert (await client.get("/api/students", headers=auth_headers)).json()["data"] == []


async def test_student_file_import_confirmation_writes_clean_preview_rows(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/students/import-file/confirm",
        json={"students": [{"student_no": "S305", "name": "周八"}]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"created": 1, "updated": 0}
    listing = (await client.get("/api/students", headers=auth_headers)).json()["data"]
    assert [(item["student_no"], item["name"]) for item in listing] == [("S305", "周八")]


async def test_student_file_import_reports_errors_and_template_downloads(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/students/import-file",
        files={"file": ("bad.csv", "学号,姓名\nS401,\nS401,\nS401,重复\n", "text/csv")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["invalid_count"] == 2
    assert len(data["errors"]) == 2

    template = await client.get("/api/students/import-template?format=xlsx", headers=auth_headers)
    assert template.status_code == 200
    assert "spreadsheetml" in template.headers["content-type"]
    assert len(template.content) > 100


async def test_student_write_requires_auth(client: AsyncClient) -> None:
    assert (
        await client.post("/api/students", json={"student_no": "S1", "name": "x"})
    ).status_code == 401
    assert (
        await client.patch("/api/students/1", json={"name": "x"})
    ).status_code == 401
    assert (await client.delete("/api/students/1")).status_code == 401
    assert (
        await client.post("/api/students/import", json={"students": []})
    ).status_code == 401
