"""validate_expense 子函数测试（P0-1 §4.1-4.4 检查项）。

测试按子函数分块：amount / tax_sum / cross_duplicate / vouchers / budget。
聚合函数 validate_expense 在 test_validate_expense.py::test_validate_expense_*。
"""
from decimal import Decimal

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