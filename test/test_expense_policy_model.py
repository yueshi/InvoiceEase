"""ExpensePolicy 模型测试（P1 费用标准政策，v1.1 §4.2/§4.3）。"""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models.expense_policy import ExpensePolicy


def test_policy_creation(db):
    p = ExpensePolicy(tenant_id="default", category="travel", item_key="accommodation",
                      city_tier="default", standard=Decimal("500.00"),
                      tolerance=Decimal("50.00"), unit="per_night")
    db.add(p)
    db.commit()
    assert p.id is not None
    assert p.enabled is True
    assert p.created_at is not None


def test_policy_unique_dimension(db):
    args = dict(tenant_id="default", category="travel", item_key="meal_allowance",
                city_tier="default", standard=Decimal("100"), tolerance=Decimal("0"),
                unit="per_day")
    db.add(ExpensePolicy(**args))
    db.commit()
    db.add(ExpensePolicy(**args))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_policy_same_item_different_tier_allowed(db):
    """同标准项不同城市档不冲突（一线/二线可分别定标准）。"""
    for tier in ("default", "tier1"):
        db.add(ExpensePolicy(tenant_id="default", category="travel",
                             item_key="accommodation", city_tier=tier,
                             standard=Decimal("500"), tolerance=Decimal("50"),
                             unit="per_night"))
    db.commit()
    assert db.query(ExpensePolicy).count() == 2