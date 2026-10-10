"""P0-2 端到端：两段握手的完整路径 + 攻击面验证（v1.1 §7.5）。

场景：
1. 全链路：proposal → confirm → 落库（一次）
2. 双击/重试：同 idempotency_key 不产生第二张提案/第二张单
3. Prompt 注入：human_ack=false 拒绝（LLM 假传"用户已确认"无用）
4. 重放：同 token 二次确认拒绝
"""
from decimal import Decimal

import pytest

from invoicing.mcp import tools as mt
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.models.expense import ExpenseClaim


def _confirm(tool, prop, **kw):
    return mt.confirm_execute(token=prop["proposal_token"], tool_name=tool,
                              human_ack=True, **kw)


def test_e2e_full_flow_proposal_confirm(db, mcp_admin_auth):
    """完整两步：提案零副作用 → 确认后落库一次。"""
    prop = mt.expense_create_proposal("差旅报销", claim_type="travel")
    assert prop["proposal_token"]
    assert prop["next_step"]                      # 引导 Agent 走第二步
    assert db.query(ExpenseClaim).count() == 0    # 提案不落业务库

    out = _confirm("expense_create", prop)
    assert out["claim_no"].startswith("FY-")
    assert db.query(ExpenseClaim).count() == 1


def test_e2e_double_click_same_idempotency_key(db, mcp_admin_auth):
    """双击/网络重试：同 idempotency_key → 同一提案、同一张单。"""
    prop1 = mt.expense_create_proposal("差旅", claim_type="travel",
                                        idempotency_key="req-double-1")
    prop2 = mt.expense_create_proposal("差旅", claim_type="travel",
                                        idempotency_key="req-double-1")
    assert prop1["proposal_token"] == prop2["proposal_token"]

    out1 = _confirm("expense_create", prop1, idempotency_key="req-double-1")
    out2 = _confirm("expense_create", prop2, idempotency_key="req-double-1")
    assert out1["id"] == out2["id"]                 # 同一张单
    assert db.query(ExpenseClaim).count() == 1      # 不产生第二张


def test_e2e_confirm_replay_same_token_rejected(db, mcp_admin_auth):
    """同一 token 二次确认（无幂等键）→ 拒绝。"""
    prop = mt.expense_create_proposal("差旅", claim_type="travel")
    _confirm("expense_create", prop)
    with pytest.raises(ValueError, match="already consumed"):
        _confirm("expense_create", prop)


def test_e2e_prompt_injection_human_ack_false_blocked(db, mcp_admin_auth):
    """LLM 幻觉/注入无法绕过 human_ack：服务端强制校验，false 一律拒绝。"""
    prop = mt.expense_create_proposal("差旅", claim_type="travel")
    with pytest.raises(ValueError, match="human_ack=true"):
        mt.confirm_execute(token=prop["proposal_token"],
                           tool_name="expense_create", human_ack=False)
    assert db.query(ExpenseClaim).count() == 0  # 未落库


def test_e2e_unregistered_tool_cannot_execute(db, mcp_admin_auth):
    """软白名单：未注册的 tool_name 不可执行（P0-3 硬白名单的前置）。"""
    prop = mt.expense_create_proposal("差旅", claim_type="travel")
    with pytest.raises(KeyError):
        mt.confirm_execute(token=prop["proposal_token"],
                           tool_name="__evil_tool__", human_ack=True)


def test_e2e_declared_write_tools_all_two_phase(db, mcp_admin_auth):
    """所有注册的两段式写工具：提案工具存在且 registry 有执行体（清单一致性）。"""
    assert len(PROPOSAL_REGISTRY) == 20
    for tool_name in PROPOSAL_REGISTRY:
        prop_fn = getattr(mt, f"{tool_name}_proposal", None)
        assert prop_fn is not None, f"{tool_name} 缺 *_proposal 入口"