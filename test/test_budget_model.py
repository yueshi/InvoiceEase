"""Budget 模型测试（P0-1 validate_expense 配套）。"""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models.budget import Budget


def test_budget_creation(db):
    b = Budget(
        tenant_id="default",
        dept="eng",
        category="travel",
        period="2026-10",
        amount=Decimal("50000.00"),
        note="Q4 budget",
    )
    db.add(b)
    db.commit()
    assert b.id is not None
    assert b.amount == Decimal("50000.00")
    assert b.created_at is not None
    assert b.updated_at is not None


def test_budget_unique_constraint(db):
    """(tenant_id, dept, category, period) 唯一。"""
    b1 = Budget(
        tenant_id="default",
        dept="eng",
        category="travel",
        period="2026-10",
        amount=Decimal("100"),
    )
    db.add(b1)
    db.commit()

    b2 = Budget(
        tenant_id="default",
        dept="eng",
        category="travel",
        period="2026-10",
        amount=Decimal("200"),
    )
    db.add(b2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_budget_different_period_allowed(db):
    """不同 period 不冲突。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("100")))
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-11", amount=Decimal("200")))
    db.commit()
    assert db.query(Budget).count() == 2


def test_budget_different_category_allowed(db):
    """同 dept 不同 category 不冲突。"""
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("100")))
    db.add(Budget(tenant_id="default", dept="eng", category="office",
                  period="2026-10", amount=Decimal("200")))
    db.commit()
    assert db.query(Budget).count() == 2