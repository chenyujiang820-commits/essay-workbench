"""鉴权路由：POST /api/auth/login。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.auth import AuthManager
from app.schemas import ApiError, Envelope, LoginRequest, TokenOut, envelope

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=Envelope[TokenOut])
async def login(payload: LoginRequest, request: Request) -> dict[str, Any]:
    """教师口令登录，成功签发 Bearer Token。

    Raises:
        ApiError: 401 口令错误。
    """
    manager: AuthManager = request.app.state.auth
    if not manager.verify_login(payload.password):
        raise ApiError("口令错误", code=401, status_code=401)

    token = TokenOut(token=manager.issue_token(), expires_in=manager.ttl_seconds)
    return envelope(token.model_dump(), message="登录成功")
