"""MCP 令牌校验：实现 SDK 的 `TokenVerifier` 协议（阶段 1：静态 per-user 令牌）。

设计见 design/2026-09-13-MCP身份与权限设计.md §4.1/§4.6。

SDK 会用它装配认证链路（`AuthenticationMiddleware` → `AuthContextMiddleware` →
`RequireAuthMiddleware`），我们只需给出「令牌 → AccessToken」这一步：

- 命中令牌表且未撤销、未过期 → AccessToken(subject=user_id, scopes=…, claims=…)
- 返回 None → SDK 统一回 401 invalid_token（无需自己写响应）

**阶段 2（OAuth 2.1）只换本文件的实现**（校验 IdP 签发的 JWT），业务层零改动。
"""
import hmac
import logging
from datetime import timedelta

from mcp.server.auth.provider import AccessToken

from invoicing.config import settings
from invoicing.db import SessionLocal
from invoicing.models import McpToken, Role, User
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)

# last_used_at 写入节流：MCP 是热路径（一次对话可能几十次调用），
# 每次都写库会让 SQLite 写锁成为瓶颈；分钟级精度足够识别僵尸令牌。
_LAST_USED_THROTTLE_SECONDS = 60


def hash_token(raw: str) -> str:
    """令牌哈希（明文不落库）。与签发侧共用，避免两处算法漂移。"""
    from hashlib import sha256

    return sha256(raw.encode("utf-8")).hexdigest()


def _split_scopes(raw: str | None) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


class MCPTokenVerifier:
    """SDK `TokenVerifier` 协议实现（duck typing：只需有 verify_token）。"""

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        with SessionLocal() as db:
            return self._verify_legacy(token, db) or self._verify_personal(token, db)

    def _verify_legacy(self, token: str, db) -> AccessToken | None:
        """legacy 内建令牌：配置里的全局 token → 最小 id 的 admin。

        仅为不中断既有 WorkBuddy 配置而保留，行为与升级前等价（零中断升级）。
        新部署把 `INVOICING_MCP_TOKEN` 留空即可关闭该通道。
        """
        configured = settings.mcp_token or ""
        if not configured:
            return None
        if not hmac.compare_digest(token.encode(), configured.encode()):
            return None

        admin = (
            db.query(User).filter(User.role == Role.admin.value).order_by(User.id).first()
        )
        if admin is None:
            logger.warning("legacy MCP 令牌已配置但库内无管理员用户，拒绝认证")
            return None
        from invoicing.mcp.identity import SCOPES

        logger.info("MCP 认证使用 legacy 内建令牌（建议改用个人令牌）")
        return AccessToken(
            token=token, client_id="legacy-builtin", scopes=list(SCOPES),
            subject=str(admin.id), expires_at=None,
            claims={"iss": settings.mcp_issuer_url, "username": admin.username,
                    "role": admin.role, "tenant_id": "default",
                    "source": "legacy", "token_id": None},
        )

    def _verify_personal(self, token: str, db) -> AccessToken | None:
        row = db.query(McpToken).filter(McpToken.token_hash == hash_token(token)).first()
        if row is None:
            return None
        now = utcnow()
        if row.revoked_at is not None:
            return None
        if row.expires_at is not None and row.expires_at <= now:
            return None

        user = db.get(User, row.user_id)
        if user is None:  # 归属人被删（FK CASCADE 理论上已清）
            return None

        self._touch(db, row, now)
        return AccessToken(
            token=token, client_id=f"token-{row.token_prefix}", scopes=_split_scopes(row.scopes),
            subject=str(user.id),
            expires_at=int(row.expires_at.replace(tzinfo=None).timestamp()) if row.expires_at else None,
            resource=settings.mcp_resource_url,  # RFC 8707：声明本服务是指定受众
            claims={"iss": settings.mcp_issuer_url, "username": user.username,
                    "role": user.role, "tenant_id": row.tenant_id,
                    "source": "token", "token_id": row.id},
        )

    def _touch(self, db, row: McpToken, now) -> None:
        if row.last_used_at is not None and (now - row.last_used_at) < timedelta(
            seconds=_LAST_USED_THROTTLE_SECONDS
        ):
            return
        row.last_used_at = now
        db.commit()
