"""SQLAlchemy 异步引擎 / Session 工厂 / SQLite PRAGMA。

* 使用 ``aiosqlite`` 驱动；连接建立时开启 WAL、外键约束与 busy_timeout。
* Session 由 FastAPI 依赖 ``get_session`` 提供，随请求提交/回滚由路由负责。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import AppSettings
from app.models import Base


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """在每个新连接上设置 SQLite PRAGMA（WAL / 外键 / 忙等待）。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_engine(settings: AppSettings) -> AsyncEngine:
    """创建异步引擎（未开启 echo，避免照片处理日志噪声）。"""
    url = f"sqlite+aiosqlite:///{settings.db_path.as_posix()}"
    engine = create_async_engine(url, future=True, echo=False)
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建 Session 工厂（``expire_on_commit=False``，便于提交后继续读取 ORM 对象）。"""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """建表（幂等）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：从 ``app.state.session_factory`` 取一个 Session。"""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
