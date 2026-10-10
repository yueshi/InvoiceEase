"""confirm_execute 单一入口测试（P0-2 两段握手第二步，v1.1 §7.5）。"""
from decimal import Decimal

import pytest

from invoicing.idempotency import create_proposal
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.mcp import tools as mt
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

# ---- final review 发现：幂等命名空间必须含主体 ------------------------------

def test_confirm_execute_idempotency_key_not_shared_across_actors(db, mcp_auth):
    """不同主体用同一 idempotency_key 不得互相命中缓存。

    否则财务 B 的 approve 会返回 A 的缓存结果且**不执行** —— 静默假成功。
    """
    from invoicing.models import Role, User

    a = User(username="u_idem_a", password_hash="x", role=Role.admin.value)
    b = User(username="u_idem_b", password_hash="x", role=Role.admin.value)
    db.add_all([a, b])
    db.commit()

    calls = []

    def _execute(db_, *, note="", **kw):
        calls.append(note)
        return {"ok": True, "note": note}

    PROPOSAL_REGISTRY[_DUMMY] = _execute
    try:
        # A 发起并确认
        mcp_auth(a)
        pa = create_proposal(db, tool_name=_DUMMY, payload={"note": "A"},
                             preview={}, actor_id=a.id, actor_type="user", channel="mcp")
        ra = confirm_execute(token=pa.token, tool_name=_DUMMY, human_ack=True,
                             idempotency_key="same-key")
        assert ra == {"ok": True, "note": "A"}

        # B 用同一 key → 必须真执行（不是拿 A 的缓存）
        mcp_auth(b)
        pb = create_proposal(db, tool_name=_DUMMY, payload={"note": "B"},
                             preview={}, actor_id=b.id, actor_type="user", channel="mcp")
        rb = confirm_execute(token=pb.token, tool_name=_DUMMY, human_ack=True,
                             idempotency_key="same-key")
        assert rb == {"ok": True, "note": "B"}, "命中他人缓存（应执行自己的）"
        assert calls == ["A", "B"]
    finally:
        PROPOSAL_REGISTRY.pop(_DUMMY, None)


def test_proposal_idempotency_key_not_shared_across_actors(db, mcp_auth):
    """proposal 阶段同理：B 的同一 key 不得拿到 A 的 token（会泄漏他人预览）。"""
    from invoicing.models import Role, User

    a = User(username="u_idem_c", password_hash="x", role=Role.admin.value)
    b = User(username="u_idem_d", password_hash="x", role=Role.admin.value)
    db.add_all([a, b])
    db.commit()

    mcp_auth(a)
    r1 = mt.expense_create_proposal("A 的单", claim_type="travel",
                                     idempotency_key="dup-key")
    mcp_auth(b)
    r2 = mt.expense_create_proposal("B 的单", claim_type="travel",
                                     idempotency_key="dup-key")
    assert r1["proposal_token"] != r2["proposal_token"], "跨主体复用了提案缓存"
    assert "B 的单" in r2["preview"]["description"]

def test_confirm_execute_consumed_error_is_actionable(db, dummy_tool):
    """执行失败后 token 已烧：重试的错误必须说清「该怎么办」，不是干巴巴 already consumed。

    final review 发现：Agent 按手册"重试"反而拿到误导性错误（真实错误如
    validate_expense FAIL 已被吞掉），需要明确指引重新发起提案。
    """
    p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)

    with pytest.raises(ValueError) as ei:
        confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    msg = str(ei.value)
    assert "already consumed" in msg
    assert "_proposal" in msg          # 指引：重新发起提案
    assert "重新" in msg


def test_confirm_execute_execution_failure_propagates_real_error(db, mcp_auth):
    """执行体抛错时，原始错误必须原样冒泡（不被吞成 already consumed）。"""
    from invoicing.models import Role, User

    u = User(username="u_fail", password_hash="x", role=Role.admin.value)
    db.add(u); db.commit()

    def _boom(db_, **kw):
        raise ValueError("validate_expense FAIL: ['AMOUNT_TOO_LARGE']")

    PROPOSAL_REGISTRY[_DUMMY] = _boom
    try:
        mcp_auth(u)
        p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                            actor_id=u.id, actor_type="user", channel="mcp")
        with pytest.raises(ValueError, match="AMOUNT_TOO_LARGE"):
            confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    finally:
        PROPOSAL_REGISTRY.pop(_DUMMY, None)


def test_confirm_execute_rejects_tool_name_mismatch(db, dummy_tool):
    """提案的 tool_name 与请求的 tool_name 必须一致（审计准确性 + 防张冠李戴）。"""
    p = create_proposal(db, tool_name="another_tool", payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    PROPOSAL_REGISTRY["another_tool"] = PROPOSAL_REGISTRY[_DUMMY]
    try:
        with pytest.raises(ValueError, match="tool_name"):
            confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    finally:
        PROPOSAL_REGISTRY.pop("another_tool", None)
    assert dummy_tool["calls"] == []


def test_confirm_execute_fails_closed_without_subject(db, dummy_tool):
    """无主体（user_id=None）→ 拒绝，不得 fail-open 放行任何提案。"""
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    tok = AccessToken(token="t", client_id="c", scopes=[], subject=None,
                      claims={"username": "anon", "role": "employee",
                              "tenant_id": "default", "source": "token",
                              "token_id": None, "iss": "test"})
    reset = auth_context_var.set(AuthenticatedUser(tok))
    try:
        p = create_proposal(db, tool_name=_DUMMY, payload={}, preview={},
                            actor_id=0, actor_type="system", channel="mcp")
        with pytest.raises(ValueError, match="主体"):
            confirm_execute(token=p.token, tool_name=_DUMMY, human_ack=True)
    finally:
        auth_context_var.reset(reset)