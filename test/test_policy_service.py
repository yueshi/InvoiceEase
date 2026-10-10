"""policy_service 测试（P1 费用标准查询 + 默认种子）。"""
from decimal import Decimal

from invoicing.models.expense_policy import ExpensePolicy
from invoicing.workflow.policy_seed import seed_default_policies
from invoicing.workflow.policy_service import get_policy, list_policies


def test_get_policy_exact_tier(db):
    db.add(ExpensePolicy(tenant_id="t1", category="travel", item_key="accommodation",
                         city_tier="tier1", standard=Decimal("600"),
                         tolerance=Decimal("50"), unit="per_night"))
    db.commit()
    p = get_policy(db, tenant_id="t1", category="travel",
                   item_key="accommodation", city_tier="tier1")
    assert p.standard == Decimal("600.00")


def test_get_policy_falls_back_to_default_tier(db):
    """未命中具体城市档 → 回落 default（不能因为没配 tier9 就判无标准）。"""
    db.add(ExpensePolicy(tenant_id="t1", category="travel", item_key="accommodation",
                         city_tier="default", standard=Decimal("500"),
                         tolerance=Decimal("50"), unit="per_night"))
    db.commit()
    p = get_policy(db, tenant_id="t1", category="travel",
                   item_key="accommodation", city_tier="tier9")
    assert p.standard == Decimal("500.00")


def test_get_policy_prefers_exact_over_default(db):
    for tier, std in (("default", "500"), ("tier1", "600")):
        db.add(ExpensePolicy(tenant_id="t1", category="travel", item_key="accommodation",
                             city_tier=tier, standard=Decimal(std),
                             tolerance=Decimal("0"), unit="per_night"))
    db.commit()
    p = get_policy(db, tenant_id="t1", category="travel",
                   item_key="accommodation", city_tier="tier1")
    assert p.standard == Decimal("600.00")


def test_get_policy_missing_returns_none(db):
    assert get_policy(db, tenant_id="t1", category="travel", item_key="nope") is None


def test_get_policy_ignores_disabled(db):
    db.add(ExpensePolicy(tenant_id="t1", category="travel", item_key="x",
                         city_tier="default", standard=Decimal("1"),
                         tolerance=Decimal("0"), unit="per_day", enabled=False))
    db.commit()
    assert get_policy(db, tenant_id="t1", category="travel", item_key="x") is None


def test_seed_default_policies_idempotent(db):
    n1 = seed_default_policies(db, tenant_id="default")
    assert n1 >= 1
    n2 = seed_default_policies(db, tenant_id="default")
    assert n2 == 0  # 二次调用不重复建


def test_seed_values_come_from_settings(db):
    """种子值必须取自 settings（spec §7.2 ✅5 不硬编码）。"""
    from invoicing.config import settings

    seed_default_policies(db, tenant_id="default")
    p = get_policy(db, tenant_id="default", category="travel",
                   item_key="meal_allowance")
    assert p.standard == Decimal(str(settings.travel_allowance_daily_standard))
    assert p.tolerance == Decimal(str(settings.over_threshold_tolerance))


def test_list_policies_by_category(db):
    seed_default_policies(db, tenant_id="default")
    rows = list_policies(db, tenant_id="default", category="travel")
    assert rows and all(r.category == "travel" for r in rows)
    assert len(list_policies(db, tenant_id="default")) > len(rows)