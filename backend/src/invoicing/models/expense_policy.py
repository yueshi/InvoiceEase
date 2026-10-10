"""费用标准政策表（v1.1 §4.2/§4.3 + §7.2 ✅5 阈值可配）。

维度：租户 × 类别 × 标准项 × 城市档。查不到具体城市档时回落 "default"
（见 workflow/policy_service.get_policy）。

与 P0-1 的 `budgets` 表的区别：
- `budgets`       = 预算余额（部门 × 类别 × 月，能花多少钱）
- `expense_policies` = 费用标准（类别 × 标准项 × 城市档，单项该花多少 + 容忍值）
两者独立：前者管"总量够不够"，后者管"单项合不合理"。
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)

from invoicing.db import Base


class ExpensePolicy(Base):
    __tablename__ = "expense_policies"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    category = Column(String(32), nullable=False, index=True)   # travel/entertainment/...
    item_key = Column(String(48), nullable=False, index=True)   # accommodation/meal_allowance/...
    city_tier = Column(String(32), nullable=False, default="default", index=True)
    standard = Column(Numeric(18, 2), nullable=False)
    tolerance = Column(Numeric(18, 2), nullable=False, default=0)
    unit = Column(String(16), nullable=False, default="per_day")  # per_day/per_night/per_person
    note = Column(String(256), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                        nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "category", "item_key", "city_tier",
                         name="uq_policy_dim"),
    )