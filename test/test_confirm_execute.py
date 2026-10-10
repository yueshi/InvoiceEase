"""confirm_execute 单一入口测试（P0-2 两段握手第二步，v1.1 §7.5）。"""
from decimal import Decimal

import pytest

from invoicing.idempotency import create_proposal
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.mcp.tools import confirm_execute
from invoicing.models import Role, User

_DUMMY = "__test_confirm_dummy__"


@pytest.fixture()
def dummy_tool(db, mcp_auth):
    """注册一个假 execute 工具；测试结束清理。

    execute 函数签名遵循约定：`(db, **payload)`（confirm_execute 传自己开的 session）。
    """
    calls = []

    def _execute(db, *, note="", **kw):
        calls.append(note or "called")
        return {"ok": True, "note": note}

    PROPOSAL_REGISTRY[_DUMMY] = _execute
    u = User(username="u_ce", password_hash="x", role=Role.employee.value)
    db.add(u)
    db.commit()
    mcp_auth(u)
    yield {"user": u, "calls": calls}
    PROPOSAL_REGISTRY.pop(_DUMMY, None)


def test_confirm_execute_unknown_tool_raises_keyerror(db, mcp_auth, dummy_tool):
    with pytest.raises(KeyError):
        confirm_execute(token="any", tool_name="__never_registered__",
                        human_ack=True)


def test_confirm_execute_invalid_token_raises(db, dummy_tool):
    with pytest.raises(ValueError, match="not found"):
        confirm_execute(token="nonexistent_token", tool_name=_DUMMY,
                        human_ack=True)


def test_confirm_execute_human_ack_required(db, dummy_tool):
    p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    with pytest.raises(ValueError, match="human_ack=true"):
        confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=False)
    # 未执行
    assert dummy_tool["calls"] == []


def test_confirm_execute_happy_path(db, dummy_tool):
    p = create_proposal(db, tool_name=_DUMMY, payload={"note": "执行我"},
                        preview={}, actor_id=1, actor_type="user", channel="mcp")
    result = confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    assert result == {"ok": True, "note": "执行我"}
    assert dummy_tool["calls"] == ["执行我"]


def test_confirm_execute_token_single_use(db, dummy_tool):
    """同一 token 消费两次 → 第二次拒绝。"""
    p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    with pytest.raises(ValueError, match="already consumed"):
        confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    assert len(dummy_tool["calls"]) == 1


def test_confirm_execute_idempotency_key_caches(db, dummy_tool):
    """同 idempotency_key 重放 → 返回缓存，不重复执行。"""
    p1 = create_proposal(db, tool_name=_DUMMY, payload={"note": "a"},
                         preview={}, actor_id=1, actor_type="user", channel="mcp")
    r1 = confirm_execute(token=p1.token, tool_name=_DUMMY, human_ack=True,
                         idempotency_key="req-1")
    assert r1 == {"ok": True, "note": "a"}
    assert len(dummy_tool["calls"]) == 1

    # 第二次（新 proposal，同 key）→ 缓存命中，不执行
    p2 = create_proposal(db, tool_name=_DUMMY, payload={"note": "b"},
                         preview={}, actor_id=1, actor_type="user", channel="mcp")
    r2 = confirm_execute(token=p2.token, tool_name=_DUMMY, human_ack=True,
                         idempotency_key="req-1")
    assert r2 == {"ok": True, "note": "a"}  # 缓存的是首次结果
    assert len(dummy_tool["calls"]) == 1


def test_confirm_execute_writes_audit(db, dummy_tool):
    """确认执行必须写审计（action=<TOOL>_CONFIRMED）。"""
    from invoicing.models.audit import AuditLog

    p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    log = (db.query(AuditLog)
           .filter(AuditLog.action == f"{_DUMMY.upper()}_CONFIRMED")
           .order_by(AuditLog.id.desc()).first())
    assert log is not None
    assert log.channel == "mcp"
    assert log.detail.get("proposal_token") == p.token


def test_confirm_execute_cross_actor_rejected(db, dummy_tool):
    """proposal 绑定发起主体：他人 token 不能消费（v1.1 §7.1 ❌7 防御）。"""
    other = User(username="u_other", password_hash="x", role=Role.employee.value)
    db.add(other)
    db.commit()
    p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                        actor_id=other.id, actor_type="user", channel="mcp")
    with pytest.raises(ValueError, match="归属主体不符"):
        confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    assert dummy_tool["calls"] == []