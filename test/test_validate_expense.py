"""validate_expense 子函数测试（P0-1 §4.1-4.4 检查项）。

测试按子函数分块：amount / tax_sum / cross_duplicate / vouchers / budget。
聚合函数 validate_expense 在 test_validate_expense.py::test_validate_expense_*。
"""
from decimal import Decimal

from invoicing.workflow.services import _check_amount


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