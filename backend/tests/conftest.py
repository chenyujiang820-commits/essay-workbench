"""pytest 全局 fixture：临时数据目录 + 临时 SQLite + 应用客户端。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from app.auth import generate_secret, hash_password
from app.config import AppSettings, get_settings
from app.db import create_engine, create_session_factory, init_db
from app.models import Issue, Student, utcnow_iso
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

TEST_PASSWORD = "test-pass-123"

_ENGINES_YAML = {
    "low_confidence_threshold": 0.85,
    "primary": {
        "base_url": "https://primary.example/v1",
        "api_key": "test-primary-key",
        "model": "primary-model",
        "timeout": 30,
        "retries": 2,
    },
    "review": {
        "base_url": "https://review.example/v1",
        "api_key": "test-review-key",
        "model": "review-model",
        "timeout": 120,
        "retries": 2,
    },
}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """创建临时数据目录并写入确定的 app.yaml / engines.yaml。"""
    root = tmp_path / "data"
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "auth": {
                    "password_hash": hash_password(TEST_PASSWORD),
                    "token_secret": generate_secret(),
                    "token_ttl_seconds": 3600,
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (config_dir / "engines.yaml").write_text(
        yaml.safe_dump(_ENGINES_YAML, allow_unicode=True), encoding="utf-8"
    )

    monkeypatch.setenv("EWB_DATA_DIR", str(root))
    monkeypatch.setenv("EWB_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        yield root
    finally:
        get_settings.cache_clear()


@pytest.fixture
def settings(data_dir: Path) -> AppSettings:
    """当前测试下的应用配置。"""
    return get_settings()


@pytest_asyncio.fixture
async def engine(settings: AppSettings) -> AsyncIterator[AsyncEngine]:
    """临时 SQLite 引擎（已建表）。"""
    eng = create_engine(settings)
    await init_db(eng)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session 工厂。"""
    return create_session_factory(engine)


SeedFn = Callable[..., Awaitable[tuple[int, int]]]


@pytest_asyncio.fixture
async def seed(session_factory: async_sessionmaker[AsyncSession]) -> SeedFn:
    """写入 1 名学生 + 1 期，返回 (student_id, issue_id)。"""

    async def _seed(student_no: str = "S001", name: str = "张三", issue_no: int = 1) -> tuple[int, int]:
        async with session_factory() as session:
            student = Student(
                student_no=student_no, name=name, active=1, created_at=utcnow_iso()
            )
            issue = Issue(
                issue_no=issue_no, week_start_date="2026-09-07", created_at=utcnow_iso()
            )
            session.add_all([student, issue])
            await session.commit()
            await session.refresh(student)
            await session.refresh(issue)
            return student.id, issue.id

    return _seed


@pytest_asyncio.fixture
async def client(data_dir: Path) -> AsyncIterator[AsyncClient]:
    """启动完整 FastAPI 应用（Worker 关闭）的异步测试客户端。"""
    from app.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            yield async_client


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """登录并返回带 Bearer token 的请求头。"""
    response = await client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    token = response.json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}
