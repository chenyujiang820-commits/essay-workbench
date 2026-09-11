"""鉴权路由：POST /api/auth/login（含 OPT-03 登录失败限速）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.auth import AuthManager, LoginRateLimiter, login_client_key
from app.schemas import ApiError, Envelope, LoginRequest, TokenOut, envelope

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _limiter(request: Request) -> LoginRateLimiter:
    """取（或懒建）本应用的登录限速器。

    放在 ``app.state`` 而非模块级单例：限速是"一个应用实例"的状态，挂在 app 上
    既满足进程内存储，又让测试里每个临时 app 天然隔离，不会互相串计。
    """
    limiter = getattr(request.app.state, "login_limiter", None)
    if not isinstance(limiter, LoginRateLimiter):
        limiter = LoginRateLimiter()
        request.app.state.login_limiter = limiter
    return limiter


@router.post("/login", response_model=Envelope[TokenOut])
async def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    """教师口令登录，成功签发 Bearer Token。

    Raises:
        ApiError: 429 失败过多被锁；401 口令错误。
    """
    manager: AuthManager = request.app.state.auth
    limiter = _limiter(request)
    key = login_client_key(request)
    limiter.ensure_allowed(key)

    if not manager.verify_login(payload.password):
        limiter.register_failure(key)
        raise ApiError("口令错误", code=401, status_code=401)

    limiter.reset(key)
    token = TokenOut(token=manager.issue_token(), expires_in=manager.ttl_seconds)
    return envelope(token.model_dump(), message="登录成功")
