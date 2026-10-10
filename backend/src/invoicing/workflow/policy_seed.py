"""默认费用标准种子（v1.1 §7.2 ✅5：阈值可配，不硬编码在 prompt）。

值全部取自 settings（或此处显式声明的一次性默认），启动时幂等播种：
已存在的 (category, item_key, city_tier) 维度跳过，运营可直接改表生效。
"""
from decimal import Decimal

from invoicing.config import settings
from invoicing.models.expense_policy import ExpensePolicy
from invoicing.workflow.policy_service import get_policy

# (category, item_key, unit, 取值)
# ponytail: 住宿/市内交通/招待的默认值是行业常见值，等真实客户配置后再改为
# 客户档案驱动；现在放这里是为了「开箱即用」，改表即覆盖。
_DEFAULTS: tuple[tuple[str, str, str, str], ...] = (
    ("travel", "meal_allowance", "per_day", "settings:travel_allowance_daily_standard"),
    ("travel", "accommodation", "per_night", "500"),
    ("travel", "local_transport_day", "per_day", "200"),
    ("entertainment", "per_head", "per_person", "300"),
)


def _value_of(spec: str) -> Decimal:
    if spec.startswith("settings:"):
        return Decimal(str(getattr(settings, spec.split(":", 1)[1])))
    return Decimal(spec)


def seed_default_policies(db, *, tenant_id: str = "default") -> int:
    """幂等播种默认标准；返回新建条数（已存在的不动）。"""
    created = 0
    for category, item_key, unit, spec in _DEFAULTS:
        if get_policy(db, tenant_id=tenant_id, category=category,
                      item_key=item_key, city_tier="default") is not None:
            continue
        db.add(ExpensePolicy(
            tenant_id=tenant_id, category=category, item_key=item_key,
            city_tier="default", standard=_value_of(spec),
            tolerance=Decimal(str(settings.over_threshold_tolerance)),
            unit=unit, note="系统默认（可在 expense_policies 表调整）",
        ))
        created += 1
    if created:
        db.commit()
    return created