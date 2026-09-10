"""FastAPI 应用入口。

职责：生命周期（配置/建表/启动 Worker/关停）、统一异常信封、路由注册、
前端 ``frontend/dist`` 的 SPA 静态托管（构建产物存在时）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import AuthManager
from app.config import get_settings
from app.db import create_engine, create_session_factory, init_db
from app.pipeline.worker import build_worker
from app.routers import auth_router, essays, exports, issues, photos, students
from app.schemas import ApiError

logger = logging.getLogger(__name__)


def _error_response(status_code: int, message: str, code: int | None = None) -> JSONResponse:
    """构造统一错误信封响应。"""
    return JSONResponse(
        status_code=status_code,
        content={"code": code if code is not None else status_code, "data": None, "message": message},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常统一为 ``{code, data:null, message}`` 信封。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.message, exc.code)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "message": "请求参数校验失败",
                "errors": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        logger.exception("未处理异常：%s", exc)
        return _error_response(500, "服务器内部错误", 500)


def _mount_frontend(app: FastAPI) -> None:
    """若前端已构建，则把 dist 挂到根路径（SPA，放在所有 /api 路由之后）。"""
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：初始化配置/数据库/Worker，退出时优雅关停。"""
    settings = get_settings()
    settings.ensure_config_files()

    engine = create_engine(settings)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.auth = AuthManager.from_settings(settings)

    worker = build_worker(settings, session_factory)
    app.state.worker = worker

    if settings.worker_enabled:
        worker.start()
        recovered = await worker.recover_pending()
        if recovered:
            logger.info("启动恢复：重新入队 %s 个未完成识别任务", recovered)

    try:
        yield
    finally:
        if settings.worker_enabled:
            await worker.stop()
        await engine.dispose()


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    application = FastAPI(
        title="班级作文工作台 API",
        version="0.1.0",
        description="双引擎 OCR + 字符级 diff + 校对定稿（一期）",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)

    application.include_router(auth_router.router)
    application.include_router(issues.router)
    application.include_router(students.router)
    application.include_router(essays.router)
    application.include_router(photos.router)
    application.include_router(exports.router)

    @application.get("/api/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        """健康检查。"""
        return {"code": 0, "data": {"status": "ok"}, "message": ""}

    _mount_frontend(application)
    return application


app = create_app()
