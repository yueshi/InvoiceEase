"""Agent 深链：Web 后台 URL 构造 + 免登票据签发。

设计见 design/2026-10-03-Agent跳转Web后台-深链与免登票据设计.md：
- 所有 MCP 出参的 URL 只在本模块拼接（工具不各自拼字符串）
- `web_base_url` 为空 → 返回 None（Agent 不显示链接，全链路零行为变化）
- 票据：一次性（兑换时以 jti 抢占，见 api/auth.py）、绑定用户、短时可配；
  兑换后签发的是该用户普通会话 token，不放大任何权限

前端契约：链接形态 `{web_base_url}/invoices?ticket=...&status=pending_review`，
守卫在 src/router/index.ts 里顺手兑换并剥票（票据不留在地址栏）。
"""
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt

from invoicing.config import settings

# 允许深链的路径前缀白名单（防未来误用拼出站外/任意路径）
_ALLOWED_PREFIXES = ("/invoices", "/expenses", "/receipts", "/tasks")


def issue_ticket(user_id: int) -> str | None:
    """签发一次性免登票据；TTL<=0 时关闭票据（返回 None，链接落登录页）。"""
    if settings.web_ticket_ttl_minutes <= 0:
        return None
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "purpose": "web_link",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=settings.web_ticket_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_ticket(ticket: str) -> dict:
    """校验票据用途并解出 claims；无效/过期抛 jwt.PyJWTError，用途不符抛 ValueError。"""
    payload = jwt.decode(ticket, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("purpose") != "web_link" or not payload.get("jti"):
        raise ValueError("票据用途不符")
    return payload


def web_link(path: str, *, user_id: int | None = None, **params) -> str | None:
    """构造 Web 后台深链；未配置 web_base_url 返回 None。

    - path 必须在白名单前缀内（ValueError 快速暴露误用）
    - params 中 None/空串的键丢弃；user_id 非空时附一次性票据
    """
    base = settings.web_base_url.rstrip("/")
    if not base:
        return None
    if not any(path == p or path.startswith(p + "/") for p in _ALLOWED_PREFIXES):
        raise ValueError(f"深链路径不在白名单: {path}")
    query = {k: v for k, v in params.items() if v not in (None, "")}
    if user_id is not None:
        ticket = issue_ticket(user_id)
        if ticket:
            query["ticket"] = ticket
    url = f"{base}{path}"
    return f"{url}?{urlencode(query)}" if query else url


def period_of(date_from: date | None, date_to: date | None) -> str | None:
    """日期区间恰为整月/整季/整年时压缩为 period 形制（YYYY-MM / YYYY-Qn / YYYY），否则 None。

    深链契约只认 period（与列表页周期选择器同构，见设计 §3.1）；
    无法恰好压缩的区间（如「本周」）退化为不带周期——链接落全部时间视图，不漏数据。
    """
    if not date_from or not date_to:
        return None
    d1, d2 = date_from, date_to
    if d1.year == d2.year and d1.month == d2.month and d1.day == 1 \
            and d2.day == monthrange(d2.year, d2.month)[1]:
        return f"{d1.year:04d}-{d1.month:02d}"
    q_start = ((d1.month - 1) // 3) * 3 + 1
    if d1.year == d2.year and d1.day == 1 and d1.month == q_start \
            and d2.month == q_start + 2 and d2.day == monthrange(d2.year, d2.month)[1]:
        return f"{d1.year:04d}-Q{(q_start + 2) // 3}"
    if (d1.month, d1.day) == (1, 1) and (d2.month, d2.day) == (12, 31) and d1.year == d2.year:
        return f"{d1.year:04d}"
    return None
