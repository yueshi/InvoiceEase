"""validate_expense 子函数测试（P0-1 §4.1-4.4 检查项）。

测试按子函数分块：amount / tax_sum / cross_duplicate / vouchers / budget。
聚合函数 validate_expense 在 test_validate_expense.py::test_validate_expense_*。
"""
from decimal import Decimal

import pytest

from invoicing.workflow.services import _check_amount, _check_tax_sum


def test_check_amount_positive_ok():
    errs = _check_amount(Decimal("100"), Decimal("5000"))
    assert errs == []


def test_check_amount_zero_fails():
    errs = _check_amount(Decimal("0"), Decimal("5000"))
    assert len(errs) == 1
    assert errs[0].code == "AMOUNT_NOT_POSITIVE"
    assert errs[0].severity == "error"


def test_check_amount_negative_fails():
    errs = _check_amount(Decimal("-100"), Decimal("5000"))
    assert errs[0].code == "AMOUNT_NOT_POSITIVE"


def test_check_amount_exceeds_threshold_fails():
    errs = _check_amount(Decimal("8000"), Decimal("5000"))
    assert any(e.code == "AMOUNT_TOO_LARGE" and e.severity == "error" for e in errs)


def test_check_amount_at_threshold_needs_review():
    """金额正好等于阈值：spec §4.2 边界 → warning 转人工。"""
    errs = _check_amount(Decimal("5000"), Decimal("5000"))
    assert any(
        e.severity == "warning" and e.code == "AMOUNT_AT_THRESHOLD" for e in errs
    )


def test_check_amount_just_below_threshold_ok():
    """4999 < 5000 → 全通过。"""
    errs = _check_amount(Decimal("4999"), Decimal("5000"))
    assert errs == []


# ===== _check_tax_sum =====

def test_check_tax_sum_balanced():
    errs = _check_tax_sum(total=Decimal("110"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert errs == []


def test_check_tax_sum_within_tolerance():
    """0.005 < 0.01 容差 → 通过。"""
    errs = _check_tax_sum(total=Decimal("110.005"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert errs == []


def test_check_tax_sum_at_tolerance_boundary_ok():
    """0.01 正好等于容差 → 通过（容差是"差不超过"，含等于）。"""
    errs = _check_tax_sum(total=Decimal("110.01"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert errs == []


def test_check_tax_sum_outside_tolerance():
    errs = _check_tax_sum(total=Decimal("110.50"), without_tax=Decimal("100"), tax=Decimal("10"))
    assert any(e.code == "TAX_SUM_MISMATCH" and e.severity == "error" for e in errs)


def test_check_tax_sum_zero_all_ok():
    errs = _check_tax_sum(total=Decimal("0"), without_tax=Decimal("0"), tax=Decimal("0"))
    assert errs == []


# ===== _check_cross_duplicate =====

def _build_claim_with_invoice_numbers(db, invoice_numbers: list[str]):
    """工厂：建一个 claim + entries + items，items 关联指定票号的 Invoice。"""
    from invoicing.models import Role, User
    from invoicing.models.expense import (
        ExpenseClaim, ExpenseEntry, ExpenseItem, EntryType,
    )
    from invoicing.models.invoice import Invoice
    from invoicing.models.fields import utcnow

    u = User(username=f"u_{id(db)}_{len(invoice_numbers)}", password_hash="x",
             role=Role.employee.value)
    db.add(u); db.flush()
    claim = ExpenseClaim(
        tenant_id="default", claim_no=f"C-{id(db)}-{len(invoice_numbers)}",
        applicant_id=u.id, title="t", claim_type=EntryType.TRAVEL.value,
        total_amount=Decimal("100"),
    )
    db.add(claim); db.flush()
    entry = ExpenseEntry(claim_id=claim.id, entry_type=EntryType.TRAVEL.value,
                         title="e", amount=Decimal("100"))
    db.add(entry); db.flush()
    for n in invoice_numbers:
        inv = Invoice(
            invoice_number=n, total_amount=Decimal("100"),
            amount_without_tax=Decimal("100"), tax_amount=Decimal("0"),
            file_url=f"f_{n}.xml", file_type="XML",
        )
        db.add(inv); db.flush()
        item = ExpenseItem(
            claim_id=claim.id, entry_id=entry.id, invoice_id=inv.id,
            voucher_type="invoice", amount=Decimal("100"),
            expense_type="travel",
        )
        db.add(item)
    db.flush()
    return claim


def test_check_cross_duplicate_no_duplicates(db):
    claim = _build_claim_with_invoice_numbers(db, ["INV001", "INV002"])
    from invoicing.workflow.services import _check_cross_duplicate
    errs = _check_cross_duplicate(db, claim)
    assert errs == []


def test_check_cross_duplicate_duplicate_invoice_numbers(db):
    """_check_cross_duplicate 是 DB uq_invoices_dedup_key 之外的双保险。

    ponytail: DB 唯一约束 (tenant_id, invoice_number) 已直接阻止同号发票共存；
    本测跳过此场景的端到端验证（实际不可达），只做 no-duplicates + blank-skip 双例。
    函数实现在手工构造的 in-memory claim 上单测覆盖（见 test_check_cross_duplicate_unit）。
    """
    pytest.skip("DB 唯一约束已直接拦截；函数行为由 unit 测试覆盖")


def test_check_cross_duplicate_unit():
    """直接对 _check_cross_duplicate 的去重算法做单测（不依赖 DB）。"""
    # 通过 mock 一个简化的 claim 对象，验证算法在重复 invoice_number 时正确报错
    from unittest.mock import MagicMock
    from invoicing.workflow.services import _check_cross_duplicate

    fake_claim = MagicMock()
    fake_claim.id = 1
    db_mock = MagicMock()
    # 模拟两次查询：一次 items 返回空，一次返回两个 invoice_number
    db_mock.query.return_value.filter.return_value.all.side_effect = [
        [(1,), (2,)],  # items.invoice_id
        [("INV001",), ("INV001",)],  # invoices.invoice_number（重复）
    ]
    errs = _check_cross_duplicate(db_mock, fake_claim)
    assert any(e.code == "DUPLICATE_INVOICE_NUMBER" for e in errs)


def test_check_cross_duplicate_skips_blank_numbers(db):
    """invoice_number 为空（无票支出/人工凭证）不参与去重。"""
    claim = _build_claim_with_invoice_numbers(db, ["INV001", ""])
    from invoicing.workflow.services import _check_cross_duplicate
    errs = _check_cross_duplicate(db, claim)
    assert errs == []