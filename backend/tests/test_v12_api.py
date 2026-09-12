"""PRD v1.2 路由契约单测：标题不传不清空、重跑识别分支、meta/health、登录限速。

对应验收：AC-4（标题）、AC-6（重跑）、AC-9（登录退避）、FR-13（meta/health）。
GAP-05 的原始缺陷是 PATCH 只带 final_text 会把已有标题顺手清空，所以本文件第一条
用例就是最有价值的那条。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from app import __version__
from app.auth import LOGIN_TOO_FREQUENT_MESSAGE, LoginRateLimiter
from app.models import Essay, Student, utcnow_iso
from app.schemas import ApiError
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# 与 conftest.TEST_PASSWORD 保持一致（沿用 test_routers.py 的字面量写法，避免跨文件 import fixture 模块）。
PASSWORD = "test-pass-123"
FINAL_TEXT = "春天来了，校园里的玉兰花开了。"


async def _student_id(
    session_factory: async_sessionmaker[AsyncSession], student_no: str = "S001"
) -> int:
    async with session_factory() as session:
        student = Student(student_no=student_no, name="张三", active=1, created_at=utcnow_iso())
        session.add(student)
        await session.commit()
        await session.refresh(student)
        return int(student.id)


async def make_essay(
    client: AsyncClient,
    headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """建学生 + 一期 + 上传 1 张，返回 essay_id（Worker 关闭，停在 uploaded）。"""
    student_id = await _student_id(session_factory)
    issue = await client.post(
        "/api/issues", json={"issue_no": 1, "week_start_date": "2026-09-07"}, headers=headers
    )
    issue_id = issue.json()["data"]["id"]
    upload = await client.post(
        f"/api/issues/{issue_id}/essays",
        data={"student_id": str(student_id)},
        files=[("files", ("a.jpg", b"fake-image-one", "image/jpeg"))],
        headers=headers,
    )
    assert upload.status_code == 202, upload.text
    return int(upload.json()["data"]["essay_id"])


async def set_status(
    session_factory: async_sessionmaker[AsyncSession], essay_id: int, status: str
) -> None:
    """直接把作文推到目标状态（不为测试搭完整识别流水线）。"""
    async with session_factory() as session:
        essay = await session.get(Essay, essay_id)
        assert essay is not None
        essay.status = status
        await session.commit()


# ---------------------------------------------------------------------------
# PATCH /essays/{id} 的 title 语义（GAP-05）
# ---------------------------------------------------------------------------
async def test_patch_without_title_keeps_existing_title(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    essay_id = await make_essay(client, auth_headers, session_factory)
    await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "title": "春天"},
        headers=auth_headers,
    )

    # 只保存定稿文字、请求体里根本没有 title：标题必须原样保留。
    saved = await client.patch(
        f"/api/essays/{essay_id}", json={"final_text": FINAL_TEXT}, headers=auth_headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["title"] == "春天"


async def test_patch_explicit_empty_title_clears_it(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """显式传空串是老师主动清空，与不传是两回事。"""
    essay_id = await make_essay(client, auth_headers, session_factory)
    await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "title": "春天"},
        headers=auth_headers,
    )

    cleared = await client.patch(
        f"/api/essays/{essay_id}", json={"final_text": FINAL_TEXT, "title": ""}, headers=auth_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["title"] == ""


async def test_patch_title_is_trimmed_and_rejects_overlong(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    essay_id = await make_essay(client, auth_headers, session_factory)
    patched = await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "title": "  我的长假  "},
        headers=auth_headers,
    )
    assert patched.json()["data"]["title"] == "我的长假"

    # schema 层 max_length=200：超长直接 422，不落库成半截标题。
    too_long = await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "title": "字" * 201},
        headers=auth_headers,
    )
    assert too_long.status_code == 422


async def test_patch_without_final_text_keeps_existing_body(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """v1.3 改口径：``final_text`` 不传即不改。

    一期这条测试钉的是"不带上正文就 400"，因为那时 PATCH 只做正文+标题+定稿三件事。
    二期要在校对页之外（看板、三榜）就地写评语/评分，若仍强迫前端回写整篇正文，
    "只改个分数"就会顺手动到定稿位 —— 所以这里新增三态语义，而不是让每个入口先去 GET 详情。
    """
    essay_id = await make_essay(client, auth_headers, session_factory)
    first = await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "proofread": True},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text

    response = await client.patch(
        f"/api/essays/{essay_id}", json={"title": "春天"}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["title"] == "春天"
    assert data["final_text"] == FINAL_TEXT
    assert data["status"] == "proofread"


async def test_patch_score_only_does_not_touch_body(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """只提交评分：正文与定稿状态都原样不动，星级一并回读（前端不再回写正文）。"""
    essay_id = await make_essay(client, auth_headers, session_factory)
    await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "proofread": True},
        headers=auth_headers,
    )

    response = await client.patch(
        f"/api/essays/{essay_id}", json={"score": 92}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["final_text"] == FINAL_TEXT
    assert data["score"] == 92
    assert data["stars"] == 5


async def test_patch_blank_final_text_is_still_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """放宽的是"不传"，不是"传空"：定稿位永远不允许被清空（成册只认 final_text）。"""
    essay_id = await make_essay(client, auth_headers, session_factory)
    await client.patch(
        f"/api/essays/{essay_id}",
        json={"final_text": FINAL_TEXT, "proofread": True},
        headers=auth_headers,
    )

    blank = await client.patch(
        f"/api/essays/{essay_id}", json={"final_text": "   "}, headers=auth_headers
    )
    assert blank.status_code == 400
    assert blank.json()["message"] == "定稿文字不能为空"
    kept = await client.get(f"/api/essays/{essay_id}", headers=auth_headers)
    assert kept.json()["data"]["final_text"] == FINAL_TEXT

# ---------------------------------------------------------------------------
# POST /essays/{id}/recognize（AC-6）
# ---------------------------------------------------------------------------
async def test_recognize_reruns_a_failed_essay(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    essay_id = await make_essay(client, auth_headers, session_factory)
    await set_status(session_factory, essay_id, "failed")

    rerun = await client.post(f"/api/essays/{essay_id}/recognize", headers=auth_headers)
    assert rerun.status_code == 202, rerun.text
    data = rerun.json()["data"]
    assert data["essay_id"] == essay_id
    assert data["status"] == "recognizing"
    assert data["task"]["step"] == "queued"
    assert data["task"]["retry_count"] >= 1

    detail = await client.get(f"/api/essays/{essay_id}", headers=auth_headers)
    assert detail.json()["data"]["status"] == "recognizing"


async def test_recognize_refuses_a_proofread_essay(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """铁律：已定稿的稿子不能被重跑覆盖掉老师成果。"""
    essay_id = await make_essay(client, auth_headers, session_factory)
    await set_status(session_factory, essay_id, "proofread")

    rerun = await client.post(f"/api/essays/{essay_id}/recognize", headers=auth_headers)
    assert rerun.status_code == 409
    assert rerun.json()["code"] == 409


async def test_recognize_requires_photos(
    client: AsyncClient,
    auth_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    essay_id = await make_essay(client, auth_headers, session_factory)
    async with session_factory() as session:
        essay = await session.get(Essay, essay_id)
        assert essay is not None
        essay.photos.clear()
        await session.commit()

    rerun = await client.post(f"/api/essays/{essay_id}/recognize", headers=auth_headers)
    assert rerun.status_code == 400
    assert rerun.json()["message"] == "该篇没有原片，无法识别"


async def test_recognize_missing_essay_is_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    rerun = await client.post("/api/essays/999999/recognize", headers=auth_headers)
    assert rerun.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/meta 与 /api/health（FR-13）
# ---------------------------------------------------------------------------
async def test_meta_is_public_and_reports_class_and_version(
    client: AsyncClient, data_dir: Path
) -> None:
    """免鉴权：登录页也要能显示班级名；且不得夹带任何学生/作文数据。"""
    app_yaml = data_dir / "config" / "app.yaml"
    config: dict[str, Any] = yaml.safe_load(app_yaml.read_text(encoding="utf-8"))
    config["class_name"] = "高一(3)班"
    app_yaml.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    response = await client.get("/api/meta")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {"class_name": "高一(3)班", "version": __version__}


async def test_health_shape(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["status"] == "ok"
    assert data["db_ok"] is True
    assert data["version"] == __version__
    # 待处理任务数是运维关心的量，键必须存在（值可以为 0）
    assert data["pending_tasks"] == 0


# ---------------------------------------------------------------------------
# 登录失败限速（AC-9 / OPT-03）
# ---------------------------------------------------------------------------
def _locked(limiter: LoginRateLimiter, key: str) -> bool:
    try:
        limiter.ensure_allowed(key)
    except ApiError as err:
        assert err.status_code == 429
        assert err.message == LOGIN_TOO_FREQUENT_MESSAGE
        return True
    return False


def test_rate_limiter_locks_after_five_failures_and_clears_on_success() -> None:
    now = [1000.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    key = "client-a"

    for _ in range(4):
        assert not _locked(limiter, key)
        limiter.register_failure(key)
    limiter.register_failure(key)  # 第 5 次失败 -> 锁 1 秒
    assert _locked(limiter, key)

    now[0] += 1.0  # 退避到期，自动解除（不给拒绝服务面）
    assert not _locked(limiter, key)

    limiter.reset(key)  # 登录成功即清零，不被历史失败拖累
    limiter.register_failure(key)
    assert not _locked(limiter, key)


def test_rate_limiter_lock_duration_doubles_each_round() -> None:
    now = [0.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    key = "b"

    for _ in range(5):
        limiter.register_failure(key)
    for seconds in (1.0, 2.0, 4.0, 8.0):
        now[0] += seconds - 0.001  # 差一点点到期：仍锁着
        assert _locked(limiter, key)
        now[0] += 0.002  # 越过错定时间：放行
        assert not _locked(limiter, key)
        limiter.register_failure(key)  # 再失败一次，下一轮退避翻倍


def test_rate_limiter_lock_is_bounded_by_the_max() -> None:
    """爆破很久之后单次锁定也不能超过 900 秒，否则等于把老师锁死。"""
    now = [0.0]
    limiter = LoginRateLimiter(max_seconds=900.0, clock=lambda: now[0])
    key = "c"
    for _ in range(60):
        limiter.register_failure(key)
        now[0] += 900.0
    assert not _locked(limiter, key)


async def test_login_returns_429_then_recovers(client: AsyncClient) -> None:
    for _ in range(5):
        bad = await client.post("/api/auth/login", json={"password": "wrong"})
        assert bad.status_code == 401, bad.text

    blocked = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.json()["message"] == LOGIN_TOO_FREQUENT_MESSAGE

    # 锁定期内连正确口令也进不去；锁会自动解除，老师不会被永久关在门外
    await asyncio.sleep(1.1)
    ok = await client.post("/api/auth/login", json={"password": PASSWORD})
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["token"]
