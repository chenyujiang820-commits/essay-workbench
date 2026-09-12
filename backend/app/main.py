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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app import __version__
from app.auth import AuthManager, LoginRateLimiter
from app.config import AppSettings, get_settings
from app.db import create_engine, create_session_factory, init_db
from app.models import RecognitionTask
from app.pipeline.worker import build_worker
from app.routers import (
    auth_router,
    awards,
    essays,
    exports,
    issues,
    photos,
    portfolio,
    shares,
    students,
)
from app.schemas import ApiError, HealthOut, MetaOut, envelope

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


#: SPA 入口文件名。
_SPA_ENTRY = "index.html"

#: 入口 HTML 的缓存策略。手机浏览器（iOS Safari / 微信 / Chrome）对**没有**
#: ``Cache-Control`` 的 HTML 做启发式缓存：重新构建后旧入口仍指向已删除的
#: ``/assets/index-<hash>.js``，而该请求的 404 会被 SPA 回退成 HTML —— 模块脚本拿到
#: HTML 后静默失败，页面白屏且没有任何报错。这是"手机端打不开"的根因之一，
#: 故入口必须每次回源校验。
_NO_CACHE = "no-cache"

#: 构建产物前缀：这些路径缺失时返回真 404，不回退成 HTML，让加载错误可见可诊断。
_ASSET_PREFIX = "assets/"


def _clean_request_path(path: str) -> str:
    """规范化挂载点内的相对路径，便于统一做前缀判断。

    starlette 传进来的形状跟平台相关（Windows 上是 ``assets\\\\a.js``，根目录是 ``.``），
    直接 ``startswith("api/")`` 这类判断在 Windows 上全部落空 —— 于是未知 ``/api/*``
    会被 SPA 回退成入口 HTML 并返回 200，前端只看到"接口没数据"。这里先把路径收敛成
    ``a/b`` 形式，再交给各判据。
    """
    cleaned = (path or "").replace("\\", "/").lstrip("/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return "" if cleaned == "." else cleaned


def _is_asset_path(path: str) -> bool:
    """请求是否指向构建产物目录。"""
    return _clean_request_path(path).startswith(_ASSET_PREFIX)


def _is_entry_path(path: str) -> bool:
    """请求是否就是 SPA 入口（根路径或 ``index.html``）。"""
    return _clean_request_path(path) in ("", _SPA_ENTRY)


class _SpaStaticFiles(StaticFiles):
    """SPA 静态托管：深层路由回退到入口 HTML，入口不缓存、缺失产物不回退。

    ``StaticFiles(html=True)`` 只把目录请求映射到 ``index.html``，不会为
    ``/present/1`` 这类前端路由做回退，直接 404。单端口部署（后端托管前端）下
    老师收藏/刷新深层链接会白屏，故对非 ``api/`` 前缀的 404 回退到 SPA 入口。

    两条配套约束（手机端真机验证暴露）：
    * 直连入口与回退得到的响应都加 ``Cache-Control: no-cache``，避免手机拿着旧入口不放；
    * ``assets/`` 前缀的 404 不回退，避免"用 HTML 顶替缺失 JS"造成无提示白屏。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        request_path = _clean_request_path(path)
        fell_back = False
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            keep_error = (
                exc.status_code != 404
                or request_path.startswith("api/")
                or _is_asset_path(request_path)
            )
            if keep_error:
                raise
            response = await super().get_response(_SPA_ENTRY, scope)
            fell_back = True
        if fell_back or _is_entry_path(request_path):
            response.headers["Cache-Control"] = _NO_CACHE
        return response


async def _probe_database(request: Request) -> tuple[bool, int | None]:
    """真跑一条 ``SELECT 1`` 并统计待处理识别任务，返回 ``(db_ok, pending_tasks)``。

    探针必须走业务同一个引擎/连接池：只有这样"进程活着但库打不开（磁盘满、文件被
    占用、权限错）"才会被 systemd / 外部拨测发现。探测失败不抛异常，交由 /api/health
    降级表达。
    """
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            stmt = select(func.count()).select_from(RecognitionTask).where(
                RecognitionTask.step != "done"
            )
            pending = (await conn.execute(stmt)).scalar_one()
        return True, int(pending)
    except Exception as exc:  # noqa: BLE001 - 健康探针须吞掉一切 DB 异常
        logger.warning("数据库探针失败：%s", exc)
        return False, None


def _mount_frontend(app: FastAPI) -> None:
    """若前端已构建，则把 dist 挂到根路径（SPA，放在所有 /api 路由之后）。"""
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir() and (dist / "index.html").exists():
        app.mount("/", _SpaStaticFiles(directory=str(dist), html=True), name="frontend")


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
    # 登录限速状态挂在 app 上：进程内内存，随应用重启清零（见 app.auth 的取舍说明）。
    app.state.login_limiter = LoginRateLimiter()

    # 并发度经 AppSettings.worker_concurrency() 钳制（1~8）后注入 Worker。
    worker = build_worker(settings, session_factory, concurrency=settings.worker_concurrency())
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
        version=__version__,
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
    # 二期（v1.3）：精选与三榜、成长档案、家长分享。
    application.include_router(awards.router)
    application.include_router(portfolio.router)
    application.include_router(shares.router)
    # 唯一免鉴权的数据面（能力令牌 + 只读 + 过期），路由内部刻意不挂 require_auth。
    application.include_router(shares.public_router)

    @application.get("/api/health", tags=["meta"])
    async def health(request: Request) -> JSONResponse:
        """健康检查（FR-13）：连通性 + 版本 + DB 可用性 + 待处理识别任务数。

        DB 不可用时 ``status="degraded"`` 且信封 ``code`` **非零**（HTTP 503）——
        v1.2 契约：``code=0`` 唯一表示成功，前端/拨测不能把降级当成正常。
        """
        db_ok, pending = await _probe_database(request)
        payload = HealthOut(
            status="ok" if db_ok else "degraded",
            version=__version__,
            db_ok=db_ok,
            pending_tasks=pending,
        )
        if db_ok:
            return JSONResponse(status_code=200, content=envelope(payload.model_dump()))
        return JSONResponse(
            status_code=503,
            content={
                "code": 503,
                "data": payload.model_dump(),
                "message": "数据库不可用",
            },
        )

    @application.get("/api/meta", tags=["meta"])
    async def meta(request: Request) -> dict[str, Any]:
        """免鉴权元信息（FR-13）：只回班级名与版本，供前端顶栏展示。

        刻意不含任何学生/作文数据，登录页也要能显示班级名。
        """
        settings: AppSettings = request.app.state.settings
        payload = MetaOut(class_name=settings.class_name, version=__version__)
        return envelope(payload.model_dump())

    _mount_frontend(application)
    return application


app = create_app()
