"""预算表：部门 × 类别 × 月（v1.1 §9.6.1 多租户 + 业务确认的三维模型）。

P0-1 validate_expense 配套。唯一键 (tenant_id, dept, category, period)。
"""
from sqlalchemy import Column, DateTime, Integer, Numeric, String, UniqueConstraint, func

from invoicing.db import Base


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    dept = Column(String(64), nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM
    amount = Column(Numeric(18, 2), nullable=False)
    note = Column(String(256), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dept", "category", "period", name="uq_budget_dim"
        ),
    )