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
from collections.abc import Callable
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
# 登录失败限速（OPT-03）
# ---------------------------------------------------------------------------
#: 连续失败达到该次数后开始锁定（下一次请求即被拒）。
LOGIN_FAILURE_LIMIT = 5

#: 首次锁定秒数，其后按 2 的幂指数退避。
LOGIN_BACKOFF_BASE_SECONDS = 1.0

#: 单次锁定最长秒数（15 分钟），避免退避失控。
LOGIN_BACKOFF_MAX_SECONDS = 900.0

#: 锁定期内的统一文案。
LOGIN_TOO_FREQUENT_MESSAGE = "尝试过于频繁，请稍后再试"


@dataclass
class _LoginBucket:
    """单个客户端标识的失败计数与锁定截止时间。"""

    failures: int = 0
    locked_until: float = 0.0


class LoginRateLimiter:
    """进程内登录失败限速器：连续失败 -> 指数退避锁（1s、2s、4s … 最长 900s）。

    设计取舍（一期为内网单用户场景，见 PRD v1.2 §7「鉴权」）：

    * **状态只放进程内存**，不建表、不引 Redis；重启即清零。配合部署侧 ``--workers 1``
      约束（见 deploy/systemd.service）才不会出现多进程各持一份计数。
    * 成功登录立即清零，避免老师正常使用时被历史失败拖累。
    * 目标是"抬高默认口令的爆破成本"，不是抗分布式伪造：锁最长 15 分钟自动解除，
      不给攻击者留下把老师本人锁在门外的拒绝服务面。
    """

    def __init__(
        self,
        *,
        failure_limit: int = LOGIN_FAILURE_LIMIT,
        base_seconds: float = LOGIN_BACKOFF_BASE_SECONDS,
        max_seconds: float = LOGIN_BACKOFF_MAX_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_limit = max(1, int(failure_limit))
        self._base_seconds = float(base_seconds)
        self._max_seconds = float(max_seconds)
        self._clock = clock
        self._buckets: dict[str, _LoginBucket] = {}

    def ensure_allowed(self, key: str) -> None:
        """锁定期内抛 429；未锁定则静默返回。

        Raises:
            ApiError: 429，文案「尝试过于频繁，请稍后再试」。
        """
        bucket = self._buckets.get(key)
        if bucket is None:
            return
        if bucket.locked_until - self._clock() > 0:
            raise ApiError(LOGIN_TOO_FREQUENT_MESSAGE, code=429, status_code=429)

    def register_failure(self, key: str) -> None:
        """记一次失败；达到阈值后按 2 的幂设定锁定时长。"""
        bucket = self._buckets.setdefault(key, _LoginBucket())
        bucket.failures += 1
        over = bucket.failures - self._failure_limit
        if over >= 0:
            seconds = min(self._max_seconds, self._base_seconds * (2.0**over))
            bucket.locked_until = self._clock() + seconds

    def reset(self, key: str) -> None:
        """登录成功：清零该客户端的失败与锁定状态。"""
        self._buckets.pop(key, None)

    def failure_count(self, key: str) -> int:
        """当前连续失败次数（测试与排障观察用）。"""
        bucket = self._buckets.get(key)
        return bucket.failures if bucket is not None else 0

    def retry_after_seconds(self, key: str) -> float:
        """剩余锁定秒数；未锁定为 0.0。"""
        bucket = self._buckets.get(key)
        if bucket is None:
            return 0.0
        return max(0.0, bucket.locked_until - self._clock())

    def clear(self) -> None:
        """清空全部状态（测试用）。"""
        self._buckets.clear()


def login_client_key(request: Request) -> str:
    """提取「客户端标识」作为限速键。

    优先取 ``X-Forwarded-For`` 首段：生产部署在 nginx 之后，``request.client.host``
    恒为 127.0.0.1，直接用它会让整座校园共用一个限速桶。

    ⚠️ 伪造风险：XFF 是客户端可自填的请求头，只有当反向代理**覆盖**该头时才可信。
    deploy/nginx.conf 目前用 ``$proxy_add_x_forwarded_for``（追加而非覆盖），首段仍可能
    是攻击者伪造值，因此本函数只是"尽力而为"的来源标识，不构成安全边界；上公网前应把
    nginx 改成 ``proxy_set_header X-Forwarded-For $remote_addr;``（或直接只信
    ``request.client.host``）。详见交付报告的风险登记。

    Returns:
        限速键；无法识别来源时回退 ``"unknown"``——宁可共享一个桶，也不要让
        "取不到来源"退化成"完全不限速"。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host or "unknown"


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
