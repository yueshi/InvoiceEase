"""expense_submit 前置 validate 测试（P0-1 §5.2 验证服务MCP 必走）。"""
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


def _make_claim(db, *, total=Decimal("110"), without_tax=Decimal("100"),
                tax=Decimal("10"), amount=Decimal("110"),
                invoice_numbers=("INV001",), dept=None, claim_type="travel",
                tenant_id="default"):
    u = User(username=f"u_sub_{id(db)}", password_hash="x", role=Role.employee.value)
    db.add(u); db.flush()
    claim = ExpenseClaim(
        tenant_id=tenant_id, claim_no=f"C-SUB-{id(db)}", applicant_id=u.id,
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
    return u, claim


def test_submit_passes_validate(db):
    """合规的 claim 顺利 submit。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("10000")))
    user, claim = _make_claim(db, dept="eng")
    result = submit_claim(db, user, claim.id)
    assert result.status.value == "pending_approval"


def test_submit_blocked_by_validate_fail(db):
    """金额超大 → validate FAIL → submit 抛错。"""
    user, claim = _make_claim(db, total=Decimal("8000"),
                                without_tax=Decimal("7272.73"),
                                tax=Decimal("727.27"),
                                amount=Decimal("8000"),
                                dept="eng")
    with pytest.raises(ValueError, match="validate_expense FAIL"):
        submit_claim(db, user, claim.id)


def test_submit_blocked_writes_audit(db):
    """FAIL 必须写 SUBMIT_BLOCKED 审计（v1.1 §9.5.3 outcome=blocked）。"""
    user, claim = _make_claim(db, total=Decimal("8000"),
                                without_tax=Decimal("7272.73"),
                                tax=Decimal("727.27"),
                                amount=Decimal("8000"),
                                dept="eng")
    with pytest.raises(ValueError):
        submit_claim(db, user, claim.id)
    log = db.query(AuditLog).filter(
        AuditLog.action == "SUBMIT_BLOCKED",
    ).order_by(AuditLog.id.desc()).first()
    assert log is not None
    # outcome 字段（v1.1 §9.5.3）尚不存在；当前 P0-1 把 outcome 放到 detail 里
    assert log.detail.get("outcome") == "blocked"
    assert log.detail.get("claim_id") == claim.id
    assert "AMOUNT_TOO_LARGE" in str(log.detail)


def test_submit_warning_only_passes(db):
    """NEEDS_REVIEW 不阻塞（仅 warning）。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("10000")))
    # 金额正好等于大额阈值 5000 → AMOUNT_AT_THRESHOLD warning
    user, claim = _make_claim(db, total=Decimal("5000"),
                                without_tax=Decimal("4545.45"),
                                tax=Decimal("454.55"),
                                amount=Decimal("5000"),
                                dept="eng")
    result = submit_claim(db, user, claim.id)
    assert result.status.value == "pending_approval"