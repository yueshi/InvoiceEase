"""MCP 访问令牌的签发与撤销（自助 + 管理员代发）。

设计见 design/2026-09-13-MCP身份与权限设计.md §4.3/§10。

安全要点（**越权防护是这里的核心职责**）：
- 员工只能为自己签发，且 scope 不得超出**本角色预设**——否则员工给自己签一个
  `invoice:admin` 就等于绕过整套 RBAC
- 管理员可为他人代发（代管/离职场景），并记录 `created_by` 便于追责
- 明文**只返回一次**，库内仅存 sha256
- 撤销即时生效（verifier 每次请求查库，无缓存）
"""
import logging
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.config import settings
from invoicing.models import McpToken, Role, User
from invoicing.models.fields import utcnow
from invoicing.mcp.identity import ROLE_DEFAULT_SCOPES, SCOPES
from invoicing.mcp.verifier import hash_token

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32  # token_urlsafe(32) ≈ 43 字符
ACTION_ISSUE = "MCP_TOKEN_ISSUE"
ACTION_REVOKE = "MCP_TOKEN_REVOKE"


def _is_admin(user: User) -> bool:
    return (user.role or "") == Role.admin.value


def allowed_scopes(actor: User) -> frozenset[str]:
    """actor 能授出的 scope 上限：管理员为全集，其余为**本角色预设**。"""
    if _is_admin(actor):
        return frozenset(SCOPES)
    return frozenset(ROLE_DEFAULT_SCOPES.get(actor.role or "", ()))


def _audit_denied(db: Session, actor: User, action: str, reason: str, **extra) -> None:
    """越权尝试要留痕（不是失败，是系统正确拦截）——与登录失败同级的安全事件。"""
    write_audit(
        db, action=action, user_id=actor.id, channel="web",
        detail={"result": "rejected", "reason": reason, **extra},
    )
    db.commit()


def issue_token(
    db: Session,
    actor: User,
    *,
    name: str,
    user_id: int | None = None,
    scopes: tuple[str, ...] | list[str] | None = None,
    expires_in_days: int | None = None,
    never_expires: bool = False,
) -> tuple[McpToken, str]:
    """签发令牌，返回 (记录, **一次性明文**)。明文不入库，调用方须立即展示。"""
    target_id = user_id if user_id is not None else actor.id
    if target_id != actor.id and not _is_admin(actor):
        _audit_denied(db, actor, ACTION_ISSUE, "non_admin_issue_for_other",
                      target_user_id=target_id)
        raise ValueError("仅系统管理员可为他人签发令牌")

    target = db.get(User, target_id)
    if target is None:
        raise ValueError(f"用户不存在: {target_id}")
    if not (name or "").strip():
        raise ValueError("请填写令牌名称（如「张三的 WorkBuddy」）")

    limit = allowed_scopes(actor)
    wanted = tuple(scopes) if scopes else ROLE_DEFAULT_SCOPES.get(target.role or "", ())
    unknown = [s for s in wanted if s not in SCOPES]
    if unknown:
        raise ValueError(f"未知权限: {'、'.join(unknown)}")
    over = [s for s in wanted if s not in limit]
    if over:
        _audit_denied(db, actor, ACTION_ISSUE, "scope_exceeds_role",
                      target_user_id=target_id, scopes=list(over))
        raise ValueError(
            f"不能授出超出你权限范围的 scope：{'、'.join(over)}"
        )
    if not wanted:
        raise ValueError("至少需要一个 scope（角色无可授权限，请先提升角色）")

    if never_expires:
        expires_at = None
    else:
        days = (
            expires_in_days
            if expires_in_days is not None
            else settings.mcp_token_default_expires_days
        )
        if days <= 0:
            raise ValueError("有效期必须为正整数天")
        expires_at = utcnow() + timedelta(days=days)

    plaintext = secrets.token_urlsafe(TOKEN_BYTES)
    row = McpToken(
        token_hash=hash_token(plaintext), token_prefix=plaintext[:8],
        name=name.strip(), user_id=target.id, tenant_id="default",
        scopes=",".join(wanted), expires_at=expires_at, created_by=actor.id,
    )
    db.add(row)
    db.flush()
    write_audit(
        db, action=ACTION_ISSUE, user_id=actor.id, channel="web",
        detail={"token_id": row.id, "name": row.name, "target_user_id": target.id,
                "scopes": list(wanted), "expires_at": str(expires_at),
                "issued_by_self": target.id == actor.id},
    )
    db.commit()
    logger.info("签发 MCP 令牌 id=%s name=%s user=%s", row.id, row.name, target.username)
    return row, plaintext


def list_tokens(db: Session, actor: User, *, all_users: bool = False) -> list[McpToken]:
    """令牌清单：默认只看自己的；管理员可看全部（用于代管与僵尸令牌清理）。"""
    q = db.query(McpToken)
    if not (_is_admin(actor) and all_users):
        q = q.filter(McpToken.user_id == actor.id)
    return q.order_by(McpToken.id.desc()).all()


def revoke_token(db: Session, actor: User, token_id: int) -> McpToken:
    """撤销令牌：本人或管理员。撤销即时生效，且**不删除**（保留追溯起点）。"""
    row = db.get(McpToken, token_id)
    if row is None:
        raise ValueError(f"令牌不存在: {token_id}")
    if row.user_id != actor.id and not _is_admin(actor):
        _audit_denied(db, actor, ACTION_REVOKE, "non_owner_revoke", token_id=token_id)
        raise ValueError("无权撤销他人的令牌")
    if row.revoked_at is not None:
        raise ValueError("令牌已撤销")

    row.revoked_at = utcnow()
    write_audit(
        db, action=ACTION_REVOKE, user_id=actor.id, channel="web",
        detail={"token_id": row.id, "name": row.name, "owner_user_id": row.user_id},
    )
    db.commit()
    return row


def token_state(row: McpToken) -> str:
    """active / revoked / expired —— 供前端展示（撤销优先于过期）。"""
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= utcnow():
        return "expired"
    return "active"
