"""P0-2 expense_* 两段握手测试（v1.1 §7.5）。

每个工具 2 例：proposal 返回 token / confirm 真正落库。
"""
from decimal import Decimal
from datetime import date

import pytest

from invoicing.mcp import tools as mt
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.models import BankReceipt, Invoice, Role, User
from invoicing.workflow import expenses as svc


def _invoice(db, number="24312000000000099001", total=Decimal("100.00")):
    inv = Invoice(
        file_url=f"{number}.xml", file_type="XML", invoice_number=number,
        status="pending_submit", verify_status="passed",
        total_amount=total, amount_without_tax=total, tax_amount=Decimal("0"),
        issue_date=date(2026, 6, 1), user_id=None, seller_name="某某公司",
    )
    db.add(inv)
    db.commit()
    return inv


# ---- 全部 7 个工具在 registry 中注册 ----------------------------------------


def test_expense_tools_registered():
    expected = {
        "expense_create", "expense_add_entry", "expense_add_invoices",
        "expense_add_receipt", "expense_add_voucher", "expense_submit",
        "expense_approve",
    }
    assert expected <= set(PROPOSAL_REGISTRY)


# ---- expense_create --------------------------------------------------------

def test_expense_create_proposal_returns_token(db, mcp_admin_auth):
    r = mt.expense_create_proposal("6 月差旅", claim_type="travel")
    assert r["proposal_token"]
    assert r["tool_name"] == "expense_create"
    assert "创建报销单" in r["preview"]["description"]


def test_expense_create_confirm_creates(db, mcp_admin_auth):
    r = mt.expense_create_proposal("6 月差旅", claim_type="travel")
    out = mt.confirm_execute(token=r["proposal_token"], tool_name="expense_create",
                             human_ack=True)
    assert out["claim_no"].startswith("FY-")
    assert out["claim_type"] == "travel"


# ---- expense_add_entry -----------------------------------------------------

def test_expense_add_entry_proposal_and_confirm(db, mcp_admin_auth):
    c = mt.confirm_execute(
        token=mt.expense_create_proposal("t", claim_type="travel")["proposal_token"],
        tool_name="expense_create", human_ack=True)
    r = mt.expense_add_entry_proposal(
        c["id"], "travel", "上海→北京 高铁", occurred_on="2026-06-10",
        scene_fields={"subtype": "transport", "transport_mode": "高铁",
                      "from_city": "上海", "to_city": "北京",
                      "vehicle_no": "G10", "travel_date": "2026-06-10"},
    )
    assert r["proposal_token"]
    entry = mt.confirm_execute(token=r["proposal_token"],
                               tool_name="expense_add_entry", human_ack=True)
    assert entry["entry_type"] == "travel"
    assert entry["title"] == "上海→北京 高铁"


# ---- expense_add_invoices --------------------------------------------------

def test_expense_add_invoices_proposal_and_confirm(db, mcp_admin_auth):
    inv = _invoice(db)
    c = mt.confirm_execute(
        token=mt.expense_create_proposal("t", claim_type="travel")["proposal_token"],
        tool_name="expense_create", human_ack=True)
    e = mt.confirm_execute(
        token=mt.expense_add_entry_proposal(
            c["id"], "travel", "打车",
            scene_fields={"subtype": "local_transport", "city": "北京",
                          "travel_date": "2026-06-12"},
        )["proposal_token"],
        tool_name="expense_add_entry", human_ack=True)
    r = mt.expense_add_invoices_proposal(c["id"], e["entry_id"], [inv.invoice_number],
                                          expense_type="travel")
    assert "加入 1 张发票" in r["preview"]["description"]
    out = mt.confirm_execute(token=r["proposal_token"],
                              tool_name="expense_add_invoices", human_ack=True)
    assert out["results"][0]["success"] is True
    assert out["total_amount"] == "100.00"


# ---- expense_add_receipt ---------------------------------------------------

def test_expense_add_receipt_proposal_and_confirm(db, mcp_admin_auth):
    r0 = BankReceipt(file_url="r.pdf", file_type="PDF", counterparty_name="某某",
                     amount=Decimal("50.00"), trade_date=date(2026, 6, 1))
    db.add(r0)
    db.commit()
    c = mt.confirm_execute(
        token=mt.expense_create_proposal("t", claim_type="other")["proposal_token"],
        tool_name="expense_create", human_ack=True)
    e = mt.confirm_execute(
        token=mt.expense_add_entry_proposal(c["id"], "other", "手续费")["proposal_token"],
        tool_name="expense_add_entry", human_ack=True)
    r = mt.expense_add_receipt_proposal(c["id"], e["entry_id"], r0.id)
    assert r["proposal_token"]
    out = mt.confirm_execute(token=r["proposal_token"],
                              tool_name="expense_add_receipt", human_ack=True)
    assert out["item"]["receipt_id"] == r0.id


# ---- expense_add_voucher ---------------------------------------------------

def test_expense_add_voucher_proposal_and_confirm(db, mcp_admin_auth):
    c = mt.confirm_execute(
        token=mt.expense_create_proposal("t", claim_type="other")["proposal_token"],
        tool_name="expense_create", human_ack=True)
    e = mt.confirm_execute(
        token=mt.expense_add_entry_proposal(c["id"], "other", "零星采购")["proposal_token"],
        tool_name="expense_add_entry", human_ack=True)
    r = mt.expense_add_voucher_proposal(
        c["id"], e["entry_id"], voucher_type="receipt_voucher", amount="100.00",
        payee_name="张三", payee_id_no="110101199001011234")
    assert r["proposal_token"]
    out = mt.confirm_execute(token=r["proposal_token"],
                              tool_name="expense_add_voucher", human_ack=True)
    assert out["item"]["voucher_type"] == "receipt_voucher"


# ---- expense_submit --------------------------------------------------------

def test_expense_submit_proposal_and_confirm(db, mcp_admin_auth):
    inv = _invoice(db, number="24312000000000099002")
    c = mt.confirm_execute(
        token=mt.expense_create_proposal("t", claim_type="travel")["proposal_token"],
        tool_name="expense_create", human_ack=True)
    e = mt.confirm_execute(
        token=mt.expense_add_entry_proposal(
            c["id"], "travel", "打车",
            scene_fields={"subtype": "local_transport", "city": "北京",
                          "travel_date": "2026-06-12"},
        )["proposal_token"],
        tool_name="expense_add_entry", human_ack=True)
    mt.confirm_execute(
        token=mt.expense_add_invoices_proposal(
            c["id"], e["entry_id"], [inv.invoice_number], expense_type="travel",
        )["proposal_token"],
        tool_name="expense_add_invoices", human_ack=True)
    r = mt.expense_submit_proposal(c["id"])
    assert "提交报销单" in r["preview"]["description"]
    out = mt.confirm_execute(token=r["proposal_token"],
                              tool_name="expense_submit", human_ack=True)
    assert out["status"] == "pending_approval"


# ---- expense_approve -------------------------------------------------------

def test_expense_approve_proposal_and_confirm(db, mcp_admin_auth):
    inv = _invoice(db, number="24312000000000099003")
    c = mt.confirm_execute(
        token=mt.expense_create_proposal("t", claim_type="travel")["proposal_token"],
        tool_name="expense_create", human_ack=True)
    e = mt.confirm_execute(
        token=mt.expense_add_entry_proposal(
            c["id"], "travel", "打车",
            scene_fields={"subtype": "local_transport", "city": "北京",
                          "travel_date": "2026-06-12"},
        )["proposal_token"],
        tool_name="expense_add_entry", human_ack=True)
    mt.confirm_execute(
        token=mt.expense_add_invoices_proposal(
            c["id"], e["entry_id"], [inv.invoice_number], expense_type="travel",
        )["proposal_token"],
        tool_name="expense_add_invoices", human_ack=True)
    mt.confirm_execute(
        token=mt.expense_submit_proposal(c["id"])["proposal_token"],
        tool_name="expense_submit", human_ack=True)

    r = mt.expense_approve_proposal(c["id"], action="approve")
    assert "通过" in r["preview"]["description"]
    out = mt.confirm_execute(token=r["proposal_token"],
                              tool_name="expense_approve", human_ack=True)
    assert out["status"] == "approved"


def test_expense_approve_reject_requires_reason(db, mcp_admin_auth):
    """驳回提案时 reason 必填（提案阶段即校验，不浪费一次确认）。"""
    with pytest.raises(ValueError, match="reason"):
        mt.expense_approve_proposal(1, action="reject")