"""MCP validate_expense 工具测试（v1.1 §5.2 验证服务MCP）。"""
from decimal import Decimal

import pytest

from invoicing.models import Role, User
from invoicing.models.expense import (
    ExpenseClaim, ExpenseEntry, ExpenseItem, EntryType,
)
from invoicing.models.invoice import Invoice
from invoicing.workflow.services import validate_expense_mcp


def _seed_claim(db, *, total=Decimal("110"), without_tax=Decimal("100"),
                tax=Decimal("10"), amount=Decimal("110"),
                invoice_numbers=("INV001",), dept=None, claim_type="travel",
                tenant_id="t1"):
    u = User(username="u_mcp", password_hash="x", role=Role.employee.value)
    db.add(u); db.flush()
    claim = ExpenseClaim(
        tenant_id=tenant_id, claim_no=f"C-MCP-{id(db)}", applicant_id=u.id,
        title="t", claim_type=claim_type, dept=dept, total_amount=total,
    )
    db.add(claim); db.flush()
    entry = ExpenseEntry(claim_id=claim.id, entry_type=EntryType.TRAVEL.value,
                         title="e", amount=amount)
    db.add(entry); db.flush()
    for n in invoice_numbers:
        inv = Invoice(
            tenant_id=tenant_id, invoice_number=n, total_amount=amount,
            amount_without_tax=without_tax, tax_amount=tax,
            file_url=f"f_{n}.xml", file_type="XML",
        )
        db.add(inv); db.flush()
        db.add(ExpenseItem(
            claim_id=claim.id, entry_id=entry.id, invoice_id=inv.id,
            voucher_type="invoice", amount=amount, expense_type=claim_type,
        ))
    db.commit()
    return claim


def test_validate_expense_mcp_pass(db):
    from invoicing.models.budget import Budget
    db.add(Budget(tenant_id="t1", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("10000")))
    claim = _seed_claim(db, dept="eng")
    r = validate_expense_mcp(db, claim.id)
    assert r["outcome"] == "PASS"
    assert r["errors"] == []
    assert r["warnings"] == []


def test_validate_expense_mcp_fail(db):
    """金额超大 → FAIL + AMOUNT_TOO_LARGE。"""
    claim = _seed_claim(db, total=Decimal("8000"),
                         without_tax=Decimal("7272.73"),
                         tax=Decimal("727.27"),
                         amount=Decimal("8000"),
                         dept="eng")
    r = validate_expense_mcp(db, claim.id)
    assert r["outcome"] == "FAIL"
    assert any(e["code"] == "AMOUNT_TOO_LARGE" for e in r["errors"])


def test_validate_expense_mcp_claim_not_found(db):
    """claim_id 不存在 → ValueError。"""
    with pytest.raises(ValueError, match="不存在或无权访问"):
        validate_expense_mcp(db, 99999)


def test_validate_expense_mcp_serializable_shape(db):
    """MCP 出参 dict 形态：outcome/errors/warnings 三键。"""
    from invoicing.models.budget import Budget
    db.add(Budget(tenant_id="t1", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("10000")))
    claim = _seed_claim(db, dept="eng")
    r = validate_expense_mcp(db, claim.id)
    assert set(r.keys()) == {"outcome", "errors", "warnings"}
    for e in r["errors"] + r["warnings"]:
        assert set(e.keys()) == {"code", "message", "severity"}