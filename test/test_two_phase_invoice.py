"""P0-2 invoice_* 两段握手测试（v1.1 §7.5）。

5 个工具各 2 例：proposal 返回 token（零副作用）/ confirm 落库。
"""
from datetime import date
from decimal import Decimal

import pytest

from invoicing.mcp import tools as mt
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.models import AuditLog, Invoice


def _seed_invoice(db, number="24312000000000088001", status="pending_review",
                  red_flag=False) -> Invoice:
    inv = Invoice(
        file_url=f"tp/{number}.xml", file_type="XML", invoice_number=number,
        status=status, total_amount=Decimal("200.00"),
        amount_without_tax=Decimal("200.00"), tax_amount=Decimal("0"),
        seller_name="原销售方", issue_date=date(2026, 8, 1),
        parse_source="XML", confidence_score=1.0, red_flag=red_flag,
    )
    db.add(inv)
    db.commit()
    return inv


def _confirm(tool, proposal):
    return mt.confirm_execute(token=proposal["proposal_token"],
                              tool_name=tool, human_ack=True)


# ---- registry --------------------------------------------------------------

def test_invoice_tools_registered():
    expected = {"invoice_update", "invoice_delete", "invoice_unblock",
                "invoice_classify", "invoice_ai_review"}
    assert expected <= set(PROPOSAL_REGISTRY)


# ---- invoice_update --------------------------------------------------------

def test_invoice_update_proposal_zero_side_effect(db, mcp_admin_auth):
    inv = _seed_invoice(db)
    prop = mt.invoice_update_proposal(inv.id, total_amount="555.00")
    assert prop["proposal_token"]
    db.expire_all()
    assert db.get(Invoice, inv.id).total_amount == Decimal("200.00")  # 未变


def test_invoice_update_confirm_applies(db, mcp_admin_auth):
    inv = _seed_invoice(db)
    out = _confirm("invoice_update",
                   mt.invoice_update_proposal(inv.id, total_amount="555.00"))
    assert Decimal(out["total_amount"]) == Decimal("555.00")


# ---- invoice_delete --------------------------------------------------------

def test_invoice_delete_proposal_preview_warns(db, mcp_admin_auth):
    inv = _seed_invoice(db)
    prop = mt.invoice_delete_proposal(inv.id)
    assert "不可撤销" in prop["preview"]["description"]
    assert db.get(Invoice, inv.id) is not None  # 提案阶段未删


def test_invoice_delete_confirm_removes(db, mcp_admin_auth):
    inv = _seed_invoice(db, number="24312000000000088002")
    inv_id = inv.id
    out = _confirm("invoice_delete", mt.invoice_delete_proposal(inv_id))
    assert out == {"ok": True}
    # 删除后用新 session 验证（本 session 的 identity map 已失效）
    from invoicing.db import SessionLocal
    with SessionLocal() as s:
        assert s.get(Invoice, inv_id) is None


# ---- invoice_unblock -------------------------------------------------------

def test_invoice_unblock_proposal_and_confirm(db, mcp_admin_auth):
    inv = _seed_invoice(db, number="24312000000000088003", status="blocked")
    inv.duplicate_flag = True  # unblock 清的是重复标记
    db.commit()
    prop = mt.invoice_unblock_proposal(inv.id)
    assert "放行" in prop["preview"]["description"]
    db.expire_all()
    assert db.get(Invoice, inv.id).status == "blocked"  # 未变

    out = _confirm("invoice_unblock", prop)
    assert out["status"] != "blocked"
    assert out["duplicate_flag"] is False


# ---- invoice_classify ------------------------------------------------------

def test_invoice_classify_proposal_and_confirm(db, mcp_admin_auth):
    inv = _seed_invoice(db, number="24312000000000088004")
    prop = mt.invoice_classify_proposal(inv.id, expense_type="travel")
    assert "travel" in prop["preview"]["description"]
    out = _confirm("invoice_classify", prop)
    assert out["expense_type"] == "travel"


def test_invoice_classify_invalid_type_rejected_at_confirm(db, mcp_admin_auth):
    inv = _seed_invoice(db, number="24312000000000088005")
    prop = mt.invoice_classify_proposal(inv.id, expense_type="bogus")
    with pytest.raises(ValueError, match="非法费用类型"):
        _confirm("invoice_classify", prop)


# ---- invoice_ai_review -----------------------------------------------------

def test_invoice_ai_review_proposal_warns_overwrite(db, mcp_admin_auth):
    inv = _seed_invoice(db, number="24312000000000088006")
    prop = mt.invoice_ai_review_proposal(inv.id)
    assert "覆盖前次结论" in prop["preview"]["description"]


def test_invoice_ai_review_confirm_unavailable_llm(db, mcp_admin_auth):
    """测试环境 LLM 关闭 → 预判不可用，确认阶段明确报错（不写半截状态）。"""
    inv = _seed_invoice(db, number="24312000000000088007")
    prop = mt.invoice_ai_review_proposal(inv.id)
    with pytest.raises(ValueError, match="预判不可用"):
        _confirm("invoice_ai_review", prop)
    db.expire_all()
    assert db.get(Invoice, inv.id).ai_review_verdict is None