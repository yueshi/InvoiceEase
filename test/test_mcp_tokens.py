"""MCP 令牌签发/撤销：越权防护、有效期、一次性明文、审计留痕。

设计见 design/2026-09-13-MCP身份与权限设计.md §4.3/§10。
"""
from datetime import timedelta

import pytest

from invoicing.config import settings
from invoicing.models import AuditLog, McpToken, Role, User
from invoicing.models.fields import utcnow
from invoicing.mcp.identity import ROLE_DEFAULT_SCOPES, SCOPES
from invoicing.mcp.verifier import hash_token
from invoicing.workflow import mcp_tokens as svc


@pytest.fixture()
def users(db):
    """不调 bcrypt：本文件校验令牌与权限，不校验口令（本机一次 hash ≈ 4.5s）。"""
    emp = User(username="emp", password_hash="x", role=Role.employee.value)
    other = User(username="other", password_hash="x", role=Role.employee.value)
    fin = User(username="fin", password_hash="x", role=Role.finance_staff.value)
    admin = User(username="adm", password_hash="x", role=Role.admin.value)
    db.add_all([emp, other, fin, admin])
    db.commit()
    return {"emp": emp, "other": other, "fin": fin, "admin": admin}


def _audits(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


# ---- 签发 ------------------------------------------------------------------


def test_issue_self_token_uses_role_preset(db, users):
    """员工自助签发：不传 scopes 时取本角色预设，明文一次性返回。"""
    row, plaintext = svc.issue_token(db, users["emp"], name="我的 WorkBuddy")

    assert row.user_id == users["emp"].id
    assert set(row.scopes.split(",")) == set(ROLE_DEFAULT_SCOPES["employee"])
    assert row.token_prefix == plaintext[:8]
    assert row.created_by == users["emp"].id  # 自助 → 本人
    assert len(plaintext) >= 40  # token_urlsafe(32)


def test_plaintext_is_not_stored(db, users):
    """明文不落库：库内只有哈希（库被拖走也不能直接拿去用）。"""
    row, plaintext = svc.issue_token(db, users["emp"], name="t")
    assert row.token_hash == hash_token(plaintext)
    assert plaintext not in (row.token_hash, row.token_prefix)
    assert db.query(McpToken).filter(McpToken.token_hash == plaintext).first() is None


def test_default_expiry_is_configured_days(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t")
    delta = row.expires_at - utcnow()
    assert abs(delta - timedelta(days=settings.mcp_token_default_expires_days)) < timedelta(minutes=1)


def test_never_expires_is_explicit(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t", never_expires=True)
    assert row.expires_at is None


def test_explicit_expiry_days(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t", expires_in_days=7)
    assert abs((row.expires_at - utcnow()) - timedelta(days=7)) < timedelta(minutes=1)


def test_issue_for_other_requires_admin(db, users):
    """员工不能替他人签发（否则可给别人开通道并甩锅）。"""
    with pytest.raises(ValueError, match="管理员"):
        svc.issue_token(db, users["emp"], name="t", user_id=users["other"].id)

    log = _audits(db, svc.ACTION_ISSUE)
    assert len(log) == 1
    assert log[0].detail["result"] == "rejected"
    assert log[0].detail["reason"] == "non_admin_issue_for_other"
    assert log[0].user_id == users["emp"].id  # 留痕到尝试者


def test_scope_escalation_denied(db, users):
    """**越权防护的核心**：员工不能给自己签出超出角色的 scope。"""
    with pytest.raises(ValueError, match="超出你权限范围"):
        svc.issue_token(db, users["emp"], name="t", scopes=("invoice:admin",))

    log = _audits(db, svc.ACTION_ISSUE)
    assert log[0].detail["reason"] == "scope_exceeds_role"
    assert "invoice:admin" in log[0].detail["scopes"]
    assert db.query(McpToken).count() == 0  # 未产生任何令牌


def test_finance_cannot_grant_admin_scope(db, users):
    """财务也不该拿到删除类权限——预设里没有就是不能签。"""
    assert "invoice:admin" not in ROLE_DEFAULT_SCOPES["finance_staff"]
    with pytest.raises(ValueError, match="超出你权限范围"):
        svc.issue_token(db, users["fin"], name="t", scopes=("invoice:admin",))


def test_admin_can_issue_any_scope_for_anyone(db, users):
    row, _ = svc.issue_token(
        db, users["admin"], name="李四的 WorkBuddy",
        user_id=users["other"].id, scopes=tuple(SCOPES),
    )
    assert row.user_id == users["other"].id
    assert row.created_by == users["admin"].id  # 代发要记谁开的
    assert set(row.scopes.split(",")) == set(SCOPES)


def test_unknown_scope_rejected(db, users):
    with pytest.raises(ValueError, match="未知权限"):
        svc.issue_token(db, users["admin"], name="t", scopes=("invoice:teleport",))


def test_requires_name_and_positive_expiry(db, users):
    with pytest.raises(ValueError, match="令牌名称"):
        svc.issue_token(db, users["emp"], name="   ")
    with pytest.raises(ValueError, match="有效期"):
        svc.issue_token(db, users["emp"], name="t", expires_in_days=0)


def test_issue_is_audited_with_success(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="我的 WorkBuddy")
    log = _audits(db, svc.ACTION_ISSUE)[0]
    assert log.user_id == users["emp"].id
    assert log.detail["token_id"] == row.id
    assert log.detail["issued_by_self"] is True


# ---- 列表与撤销 ------------------------------------------------------------


def test_list_tokens_scoped_to_self(db, users):
    svc.issue_token(db, users["emp"], name="mine")
    svc.issue_token(db, users["other"], name="theirs")

    assert [t.name for t in svc.list_tokens(db, users["emp"])] == ["mine"]
    assert len(svc.list_tokens(db, users["other"])) == 1


def test_admin_can_list_all(db, users):
    svc.issue_token(db, users["emp"], name="mine")
    svc.issue_token(db, users["other"], name="theirs")

    assert len(svc.list_tokens(db, users["admin"])) == 0  # 管理员自己也走"只看自己"
    assert len(svc.list_tokens(db, users["admin"], all_users=True)) == 2


def test_revoke_own_token(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t")
    svc.revoke_token(db, users["emp"], row.id)

    db.refresh(row)
    assert row.revoked_at is not None
    assert svc.token_state(row) == "revoked"
    assert db.query(McpToken).count() == 1  # 不删除：保留追溯起点
    assert _audits(db, svc.ACTION_REVOKE)[0].detail["token_id"] == row.id


def test_revoke_others_denied(db, users):
    row, _ = svc.issue_token(db, users["other"], name="t")
    with pytest.raises(ValueError, match="无权撤销"):
        svc.revoke_token(db, users["emp"], row.id)
    assert _audits(db, svc.ACTION_REVOKE)[0].detail["result"] == "rejected"


def test_admin_can_revoke_any(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t")
    svc.revoke_token(db, users["admin"], row.id)
    db.refresh(row)
    assert row.revoked_at is not None


def test_revoke_twice_rejected(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t")
    svc.revoke_token(db, users["emp"], row.id)
    with pytest.raises(ValueError, match="已撤销"):
        svc.revoke_token(db, users["emp"], row.id)


def test_token_state_expired(db, users):
    row, _ = svc.issue_token(db, users["emp"], name="t")
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert svc.token_state(row) == "expired"

    row.revoked_at = utcnow()  # 撤销优先于过期
    db.commit()
    assert svc.token_state(row) == "revoked"


def test_issued_token_verifies_end_to_end(db, users):
    """签发的令牌能通过 verifier —— 两端共用 hash_token，避免算法漂移。"""
    import asyncio

    from invoicing.mcp.verifier import MCPTokenVerifier

    row, plaintext = svc.issue_token(db, users["emp"], name="t", scopes=("invoice:read",))
    tok = asyncio.run(MCPTokenVerifier().verify_token(plaintext))

    assert tok is not None
    assert tok.subject == str(users["emp"].id)
    assert tok.scopes == ["invoice:read"]
    assert tok.claims["token_id"] == row.id
