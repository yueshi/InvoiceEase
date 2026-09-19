"""MCP 令牌校验：静态 per-user 令牌 → AccessToken（SDK TokenVerifier 协议）。

设计见 design/2026-09-13-MCP身份与权限设计.md §4.6（legacy 兼容）。
"""
import asyncio
from datetime import timedelta

import pytest

from invoicing.config import settings
from invoicing.models import McpToken, Role, User
from invoicing.models.fields import utcnow
from invoicing.mcp.identity import SCOPES
from invoicing.mcp.verifier import MCPTokenVerifier, hash_token


def _user(db, username="zhangsan", role=Role.employee.value):
    # 不调 bcrypt（本机一次 hash ≈ 4.5s）：本文件只校验令牌→用户映射，不看口令。
    # 若哪天真用到口令校验，这里会**响亮地**失败而不是悄悄放过。
    u = User(username=username, password_hash="not-a-real-hash", role=role)
    db.add(u)
    db.commit()
    return u


def _token(db, user, raw="tok-abc-123456", **kw):
    t = McpToken(
        token_hash=hash_token(raw), token_prefix=raw[:8], name=kw.get("name", "张三的 WorkBuddy"),
        user_id=user.id, tenant_id=kw.get("tenant_id", "default"),
        scopes=",".join(kw.get("scopes", ("invoice:read", "expense:write"))),
        expires_at=kw.get("expires_at"), revoked_at=kw.get("revoked_at"),
    )
    db.add(t)
    db.commit()
    return t


def _verify(raw: str):
    return asyncio.run(MCPTokenVerifier().verify_token(raw))


def test_valid_token_maps_to_user_and_scopes(db):
    """核心：令牌 → 具体用户 + scope + role（role 决定数据范围，供 service 层使用）。"""
    u = _user(db, username="zhangsan", role=Role.employee.value)
    t = _token(db, u, raw="tok-abc-123456", scopes=("invoice:read", "expense:write"))

    tok = _verify("tok-abc-123456")
    assert tok is not None
    assert tok.subject == str(u.id)
    assert set(tok.scopes) == {"invoice:read", "expense:write"}
    assert tok.claims["username"] == "zhangsan"
    assert tok.claims["role"] == "employee"
    assert tok.claims["tenant_id"] == "default"
    assert tok.claims["token_id"] == t.id
    assert tok.claims["source"] == "token"
    assert tok.claims["iss"] == settings.mcp_issuer_url  # R4：issuer 进 claims，多 IdP 复合键预留


def test_unknown_token_rejected(db):
    _user(db)
    assert _verify("not-a-real-token") is None


def test_revoked_token_rejected(db):
    """撤销**立即生效**（验收标准 2）：不必重启服务，不必等缓存过期。"""
    u = _user(db)
    t = _token(db, u, raw="tok-revoked-1")
    t.revoked_at = utcnow()
    db.commit()

    assert _verify("tok-revoked-1") is None


def test_expired_token_rejected(db):
    u = _user(db)
    _token(db, u, raw="tok-expired-1", expires_at=utcnow() - timedelta(seconds=1))
    assert _verify("tok-expired-1") is None


def test_token_expiring_in_future_accepted(db):
    u = _user(db)
    _token(db, u, raw="tok-future-1", expires_at=utcnow() + timedelta(days=1))
    assert _verify("tok-future-1") is not None


def test_no_expiry_accepted(db):
    """NULL = 服务身份/永久（签发默认 90 天，此路径留给显式选择）。"""
    u = _user(db)
    _token(db, u, raw="tok-forever-1", expires_at=None)
    assert _verify("tok-forever-1") is not None


def test_last_used_at_recorded_and_throttled(db):
    """记录使用时间（识别僵尸令牌），但**节流**：热路径上不每次请求都写库。"""
    u = _user(db)
    t = _token(db, u, raw="tok-usage-1")

    assert t.last_used_at is None
    _verify("tok-usage-1")
    db.refresh(t)
    first = t.last_used_at
    assert first is not None

    _verify("tok-usage-1")  # 60 秒内第二次不应重写
    db.refresh(t)
    assert t.last_used_at == first

    t.last_used_at = utcnow() - timedelta(seconds=120)  # 超过节流窗口 → 再次写入
    db.commit()
    stale = t.last_used_at
    _verify("tok-usage-1")
    db.refresh(t)
    assert t.last_used_at > stale


def test_legacy_builtin_token_maps_to_admin(db):
    """legacy 内建令牌：行为与升级前等价（零中断），但审计可辨识来源。"""
    _user(db, username="admin1", role=Role.admin.value)  # 最小 id 的 admin
    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "mcp_token", "legacy-shared-token")
    try:
        tok = _verify("legacy-shared-token")
    finally:
        monkey.undo()

    assert tok is not None
    assert tok.claims["source"] == "legacy"
    assert tok.claims["token_id"] is None
    assert tok.claims["role"] == Role.admin.value
    assert set(tok.scopes) == set(SCOPES)  # 与现状等价：管理员通道不受 scope 限制


def test_legacy_disabled_when_config_empty(db):
    """新部署把 mcp_token 留空 → legacy 通道关闭（不再有全局管理员令牌）。"""
    _user(db, username="admin1", role=Role.admin.value)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "mcp_token", "")
    try:
        assert _verify("") is None
    finally:
        monkey.undo()


def test_legacy_token_default_off():
    """M1：legacy 内建令牌默认关闭（config 默认空串）——钉死字段默认值，防回归。"""
    from invoicing.config import Settings

    assert Settings.model_fields["mcp_token"].default == ""
