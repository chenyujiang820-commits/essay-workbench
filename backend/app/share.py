"""家长分享令牌（v1.3 / FR-07）：生成、有效期计算与有效性判定。

单独成模块而不是塞进路由，是为了让"过期"这个判据可被单测穷举：令牌一旦流出到家长群，
``expires_at`` 就是唯一的止损手段，判错方向的代价比一般字段高。

时间口径与全库一致：ISO 8601 UTC 字符串（见 ``app.models``），比较时解析成带时区的
datetime —— 直接比字符串会在 ``Z`` 与 ``+00:00`` 两种写法之间悄悄判错。
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: 令牌字节数：32 -> ``token_urlsafe`` 得到 43 个 URL 安全字符，不可枚举。
TOKEN_BYTES = 32
MIN_SHARE_DAYS = 1
MAX_SHARE_DAYS = 90
DEFAULT_SHARE_DAYS = 14

#: 失效统一文案：**不区分**"不存在 / 已撤销 / 已过期"。
#: 区分它们等于给令牌做枚举预言机（"这个存在但过期了"就泄露了一位信息）。
SHARE_GONE_MESSAGE = "链接已失效或不存在，请向老师重新获取"


def new_share_token() -> str:
    """生成随机分享令牌。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def share_path(token: str) -> str:
    """分享页的站内相对路径。

    刻意不拼绝对 URL：后端并不知道自己对外暴露的域名（内网 IP 时甚至是错的），
    交给前端按 ``window.location`` 拼才不会同时对内网和公网说谎。
    """
    return f"/share/{token}"


def expiry_iso(days: int) -> str:
    """``days`` 天后的过期时刻（ISO 8601 UTC，秒精度）。

    Raises:
        ValueError: 天数越界（由路由转成 400/422，不做静默钳制）。
    """
    if not MIN_SHARE_DAYS <= days <= MAX_SHARE_DAYS:
        raise ValueError(f"有效期天数必须在 {MIN_SHARE_DAYS}~{MAX_SHARE_DAYS} 之间")
    moment = datetime.now(UTC) + timedelta(days=days)
    return moment.replace(microsecond=0).isoformat()


def is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    """链接是否已过期；**时间戳解析不了时按已过期处理**（宁可拒绝对外可读）。"""
    current = now or datetime.now(UTC)
    try:
        moment = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        logger.warning("分享链接过期时间戳无法解析，按已过期处理：%r", expires_at)
        return True
    if moment.tzinfo is None:
        # 无时区的历史值按 UTC 解释：既避免 naive/aware 比较直接抛异常，
        # 也不会把"本地时间看起来还没到"当成未过期。
        moment = moment.replace(tzinfo=UTC)
    return moment <= current


def is_active(revoked: int, expires_at: str, *, now: datetime | None = None) -> bool:
    """综合判定：未撤销且未过期。"""
    return not int(revoked or 0) and not is_expired(expires_at, now=now)