"""idempotency 工具模块测试（P0-2 两段握手核心，v1.1 §7.5）。"""
import pytest

from invoicing.idempotency import (
    consume_proposal,
    create_proposal,
    generate_token,
    idempotent_run,
)


# ===== generate_token =====

def test_generate_token_url_safe_and_long():
    t = generate_token()
    assert len(t) >= 32
    assert all(c.isalnum() or c in "-_" for c in t)


def test_generate_token_unique():
    tokens = {generate_token() for _ in range(50)}
    assert len(tokens) == 50


# ===== idempotent_run =====

def test_idempotent_run_caches_result(db):
    calls = []

    def fn():
        calls.append(1)
        return {"result": "first"}

    r1 = idempotent_run(db, key="k1", tool_name="t1", fn=fn)
    assert r1 == {"result": "first"}
    assert len(calls) == 1

    r2 = idempotent_run(db, key="k1", tool_name="t1", fn=fn)
    assert r2 == {"result": "first"}
    assert len(calls) == 1  # 未再调 fn


def test_idempotent_run_no_key_calls_each_time(db):
    calls = []

    def fn():
        calls.append(1)
        return {"n": len(calls)}

    r1 = idempotent_run(db, key=None, tool_name="t1", fn=fn)
    r2 = idempotent_run(db, key=None, tool_name="t1", fn=fn)
    assert r1["n"] == 1
    assert r2["n"] == 2


def test_idempotent_run_different_tool_no_collision(db):
    """同 key 不同 tool 各自独立执行。"""
    calls = []

    def fn_a():
        calls.append("a")
        return {"tool": "a"}

    def fn_b():
        calls.append("b")
        return {"tool": "b"}

    ra = idempotent_run(db, key="same", tool_name="tool_a", fn=fn_a)
    rb = idempotent_run(db, key="same", tool_name="tool_b", fn=fn_b)
    assert ra == {"tool": "a"}
    assert rb == {"tool": "b"}
    assert calls == ["a", "b"]


# ===== create_proposal / consume_proposal =====

def test_create_proposal_returns_token(db):
    from invoicing.models.proposal import Proposal

    p = create_proposal(db, tool_name="expense_create",
                        payload={"title": "t"}, preview={"desc": "..."},
                        actor_id=1, actor_type="user", channel="mcp")
    assert p.token is not None
    assert p.expires_at is not None
    assert p.consumed_at is None
    assert db.query(Proposal).count() == 1


def test_consume_proposal_marks_consumed(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    consumed = consume_proposal(db, token=p.token, human_ack=True)
    assert consumed.consumed_at is not None
    assert consumed.token == p.token


def test_consume_proposal_human_ack_required(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    with pytest.raises(ValueError, match="human_ack=true"):
        consume_proposal(db, token=p.token, human_ack=False)
    # 未消费
    db.refresh(p)
    assert p.consumed_at is None


def test_consume_proposal_expired_rejected(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp",
                        ttl_seconds=-1)  # 立即过期
    with pytest.raises(ValueError, match="expired"):
        consume_proposal(db, token=p.token, human_ack=True)


def test_consume_proposal_already_consumed_rejected(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                        actor_id=1, actor_type="user", channel="mcp")
    consume_proposal(db, token=p.token, human_ack=True)
    with pytest.raises(ValueError, match="already consumed"):
        consume_proposal(db, token=p.token, human_ack=True)


def test_consume_proposal_unknown_token_rejected(db):
    with pytest.raises(ValueError, match="not found"):
        consume_proposal(db, token="nonexistent", human_ack=True)