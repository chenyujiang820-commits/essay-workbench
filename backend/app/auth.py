"""口令登录与 Bearer Token 签发/校验。

* 口令以 PBKDF2-SHA256 哈希形式存于数据目录 ``config/app.yaml``（明文绝不落库）。
* Token 为自包含的 HMAC-SHA256 签名串：``base64url(payload).base64url(signature)``，
  payload 为 ``{"sub": "...", "exp": <unix_ts>}``；无状态、无服务端会话。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
from dataclasses import dataclass

from fastapi import Request

from app.config import AppSettings
from app.schemas import ApiError

PBKDF2_ITERATIONS = 200_000
_PBKDF2_ALGO = "pbkdf2_sha256"
DEFAULT_TOKEN_TTL_SECONDS = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# 口令哈希
# ---------------------------------------------------------------------------
def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = PBKDF2_ITERATIONS,
) -> str:
    """生成口令哈希：``pbkdf2_sha256$iterations$salt_hex$digest_hex``。

    Args:
        password: 明文口令（不得为空）。
        salt: 自定义盐（测试用）；缺省随机 16 字节。
        iterations: PBKDF2 迭代次数。

    Returns:
        可安全存储的哈希字符串。

    Raises:
        ValueError: 口令为空。
    """
    if not password:
        raise ValueError("password must not be empty")
    salt_bytes = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    return f"{_PBKDF2_ALGO}${iterations}${salt_bytes.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验口令；格式非法时返回 ``False``。"""
    if not password or not stored:
        return False
    try:
        algo, iter_text, salt_hex, digest_hex = stored.split("$")
        if algo != _PBKDF2_ALGO:
            return False
        iterations = int(iter_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def generate_secret(nbytes: int = 32) -> str:
    """生成随机令牌密钥（用于 HMAC 签名）。"""
    return secrets.token_urlsafe(nbytes)


# ---------------------------------------------------------------------------
# Token 编解码
# ---------------------------------------------------------------------------
def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True)
class TokenClaims:
    """校验通过的 Token 载荷。"""

    subject: str
    expires_at: int


# ---------------------------------------------------------------------------
# 鉴权管理器
# ---------------------------------------------------------------------------
class AuthManager:
    """持有口令哈希与签名密钥，负责登录校验与 Token 生命周期。"""

    def __init__(
        self,
        password_hash: str,
        token_secret: str,
        ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    ) -> None:
        self._password_hash = password_hash
        self._secret = token_secret.encode("utf-8")
        self._ttl = int(ttl_seconds)

    @classmethod
    def from_settings(cls, settings: AppSettings, *, default_password: str = "admin123") -> AuthManager:
        """从 ``app.yaml`` 构造；配置缺失时回退到默认口令与随机密钥。"""
        raw = settings.app_config().get("auth")
        config: dict[str, object] = raw if isinstance(raw, dict) else {}

        stored_hash = config.get("password_hash")
        password_hash = str(stored_hash) if isinstance(stored_hash, str) and stored_hash else hash_password(default_password)

        stored_secret = config.get("token_secret")
        token_secret = str(stored_secret) if isinstance(stored_secret, str) and stored_secret else generate_secret()

        raw_ttl = config.get("token_ttl_seconds", DEFAULT_TOKEN_TTL_SECONDS)
        try:
            ttl = int(str(raw_ttl))
        except (TypeError, ValueError):
            ttl = DEFAULT_TOKEN_TTL_SECONDS

        return cls(password_hash, token_secret, ttl)

    @property
    def ttl_seconds(self) -> int:
        """Token 有效期（秒）。"""
        return self._ttl

    def verify_login(self, password: str) -> bool:
        """校验登录口令。"""
        return verify_password(password, self._password_hash)

    def issue_token(self, subject: str = "teacher", *, now: float | None = None) -> str:
        """签发 Token。"""
        issued_at = int(now if now is not None else time.time())
        payload = json.dumps(
            {"sub": subject, "exp": issued_at + self._ttl},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return f"{_b64encode(payload)}.{_b64encode(signature)}"

    def verify_token(self, token: str, *, now: float | None = None) -> TokenClaims | None:
        """校验 Token；非法/过期返回 ``None``。"""
        if not token:
            return None
        try:
            payload_b64, signature_b64 = token.split(".", 1)
            payload = _b64decode(payload_b64)
            signature = _b64decode(signature_b64)
        except (ValueError, TypeError):
            return None

        expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            return None

        try:
            claims = json.loads(payload.decode("utf-8"))
            expires_at = int(claims["exp"])
        except (ValueError, KeyError, TypeError):
            return None

        current = int(now if now is not None else time.time())
        if expires_at < current:
            return None
        return TokenClaims(subject=str(claims.get("sub", "teacher")), expires_at=expires_at)


# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------
async def require_auth(request: Request) -> str:
    """FastAPI 依赖：校验 ``Authorization: Bearer <token>``，返回 subject。

    Raises:
        ApiError: 401 未授权（缺少/无效/过期 token）。
    """
    manager: AuthManager | None = getattr(request.app.state, "auth", None)
    if manager is None:
        raise ApiError("鉴权组件未初始化", code=401, status_code=401)

    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError("未授权：缺少 Bearer token", code=401, status_code=401)

    token = header[len("bearer ") :].strip()
    claims = manager.verify_token(token)
    if claims is None:
        raise ApiError("未授权：token 无效或已过期", code=401, status_code=401)
    return claims.subject


def _main() -> None:  # pragma: no cover - 命令行辅助
    """``python -m app.auth hash <口令>`` 生成口令哈希。"""
    if len(sys.argv) >= 3 and sys.argv[1] == "hash":
        print(hash_password(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "secret":
        print(generate_secret())
    else:
        print("usage: python -m app.auth hash <password> | python -m app.auth secret")


if __name__ == "__main__":  # pragma: no cover
    _main()
