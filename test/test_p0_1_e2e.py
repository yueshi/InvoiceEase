"""P0-1 端到端：一张报销从 validate 到 submit 完整路径。"""
from decimal import Decimal

import pytest

from invoicing.models import Role, User
from invoicing.models.audit import AuditLog
from invoicing.models.budget import Budget
from invoicing.models.expense import (
    ExpenseClaim, ExpenseEntry, ExpenseItem, EntryType,
)
from invoicing.models.invoice import Invoice
from invoicing.workflow.expenses import submit_claim
from invoicing.workflow.services import validate_expense
from invoicing.workflow.validation import ValidationOutcome


def _seed_claim(db, *, total=Decimal("110"), without_tax=Decimal("100"),
                tax=Decimal("10"), amount=Decimal("110"),
                invoice_numbers=("INV001",), dept="eng", claim_type="travel"):
    u = User(username=f"u_e2e_{id(db)}", password_hash="x", role=Role.employee.value)
    db.add(u); db.flush()
    claim = ExpenseClaim(
        tenant_id="default", claim_no=f"C-E2E-{id(db)}", applicant_id=u.id,
        title="t", claim_type=claim_type, dept=dept, total_amount=total,
    )
    db.add(claim); db.flush()
    entry = ExpenseEntry(claim_id=claim.id, entry_type=EntryType.TRAVEL.value,
                         title="e", amount=amount)
    db.add(entry); db.flush()
    for n in invoice_numbers:
        inv = Invoice(
            tenant_id="default", invoice_number=n, total_amount=amount,
            amount_without_tax=without_tax, tax_amount=tax,
            file_url=f"f_{n}.xml", file_type="XML",
        )
        db.add(inv); db.flush()
        db.add(ExpenseItem(
            claim_id=claim.id, entry_id=entry.id, invoice_id=inv.id,
            voucher_type="invoice", amount=amount, expense_type=claim_type,
        ))
    db.commit()
    return u, claim


def test_e2e_full_pass(db):
    """1) validate PASS  2) submit 走通  3) 状态进 pending_approval。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("10000")))
    user, claim = _seed_claim(db, dept="eng")

    r = validate_expense(db, claim)
    assert r.outcome == ValidationOutcome.PASS

    result = submit_claim(db, user, claim.id)
    assert result.status.value == "pending_approval"

    # 审计：EXPENSE_SUBMIT 出现
    sub_log = db.query(AuditLog).filter(
        AuditLog.action == "EXPENSE_SUBMIT",
    ).order_by(AuditLog.id.desc()).first()
    assert sub_log is not None
    assert sub_log.channel == "web"


def test_e2e_blocked_with_audit(db):
    """1) validate FAIL  2) submit 抛错  3) SUBMIT_BLOCKED 审计留痕。"""
    user, claim = _seed_claim(db, total=Decimal("8000"),
                              without_tax=Decimal("7272.73"),
                              tax=Decimal("727.27"),
                              amount=Decimal("8000"),
                              dept="eng")

    r = validate_expense(db, claim)
    assert r.outcome == ValidationOutcome.FAIL

    with pytest.raises(ValueError, match="validate_expense FAIL"):
        submit_claim(db, user, claim.id)

    # 审计
    block_log = db.query(AuditLog).filter(
        AuditLog.action == "SUBMIT_BLOCKED",
    ).order_by(AuditLog.id.desc()).first()
    assert block_log is not None
    assert block_log.detail.get("claim_id") == claim.id
    assert "AMOUNT_TOO_LARGE" in str(block_log.detail)


def test_e2e_over_budget_blocks(db):
    """1) 配置预算 5000  2) 报销 8000  3) 超预算 → OVER_BUDGET FAIL。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("5000")))
    user, claim = _seed_claim(db, total=Decimal("8000"),
                              without_tax=Decimal("7272.73"),
                              tax=Decimal("727.27"),
                              amount=Decimal("8000"),
                              dept="eng")

    r = validate_expense(db, claim)
    assert r.outcome == ValidationOutcome.FAIL
    codes = {e.code for e in r.errors}
    assert "OVER_BUDGET" in codes
    assert "AMOUNT_TOO_LARGE" in codes  # 同时触发金额阈值


def test_e2e_warning_only_passes(db):
    """金额正好等于大额阈值 → warning → NEEDS_REVIEW → submit 走通。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("10000")))
    user, claim = _seed_claim(db, total=Decimal("5000"),
                              without_tax=Decimal("4545.45"),
                              tax=Decimal("454.55"),
                              amount=Decimal("5000"),
                              dept="eng")

    r = validate_expense(db, claim)
    assert r.outcome == ValidationOutcome.NEEDS_REVIEW
    assert any(w.code == "AMOUNT_AT_THRESHOLD" for w in r.warnings)

    # submit 仍走通（NEEDS_REVIEW 不阻塞）
    result = submit_claim(db, user, claim.id)
    assert result.status.value == "pending_approval"