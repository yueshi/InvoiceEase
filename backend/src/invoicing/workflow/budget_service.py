"""预算 service：query_budget / check_available（v1.1 §9.6 配套）。

P0-1 validate_expense 依赖此模块做预算余额检查。
"""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from invoicing.models.budget import Budget
from invoicing.mcp.identity import requires


@dataclass
class BudgetView:
    allocated: Decimal
    used: Decimal
    available: Decimal


@dataclass
class BudgetCheck:
    available: bool
    remaining: Decimal
    overage: Decimal  # 正数表示超支金额


def query_budget(db, *, tenant_id: str, dept: str, category: str,
                 period: str) -> BudgetView:
    """查询 (dept, category, period) 预算视图。

    ponytail: used=0 ceiling — 完整 used 需按 (dept, category, period) 聚合
    ExpenseClaim.total_amount；当前 ExpenseClaim 缺 dept/expense_type 字段，
    等报销单 schema 加字段后再启用 used 计算（task 待补）。
    """
    budget = db.execute(
        select(Budget).where(
            Budget.tenant_id == tenant_id,
            Budget.dept == dept,
            Budget.category == category,
            Budget.period == period,
        )
    ).scalar_one_or_none()

    allocated = budget.amount if budget else Decimal("0")
    used = Decimal("0")  # ponytail: see docstring above
    return BudgetView(
        allocated=allocated,
        used=used,
        available=allocated - used,
    )


def check_available(db, *, tenant_id: str, dept: str, category: str,
                    period: str, amount: Decimal) -> BudgetCheck:
    """检查预算是否可承担该金额。"""
    view = query_budget(db, tenant_id=tenant_id, dept=dept,
                         category=category, period=period)
    remaining = view.available - amount
    return BudgetCheck(
        available=remaining >= 0,
        remaining=view.available,
        overage=-remaining if remaining < 0 else Decimal("0"),
    )


# ===== MCP 工具（v1.1 §5.2 预算服务MCP） =====
# ponytail: 当前实现为 thin wrapper；后续可拆到 mcp/budget.py 独立模块。

_DEFAULT_TENANT = "default"


def _current_period() -> str:
    from datetime import date
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


@requires("expense:read")
def query_budget_mcp(db, *, dept: str, category: str,
                     period: str | None = None) -> dict:
    """MCP 工具：查询预算（v1.1 §5.2 预算服务MCP）。

    scope: expense:read（P0-2 裁决——不新增 budget:read）。
    """
    v = query_budget(db, tenant_id=_DEFAULT_TENANT, dept=dept,
                     category=category, period=period or _current_period())
    return {
        "allocated": str(v.allocated),
        "used": str(v.used),
        "available": str(v.available),
    }


@requires("expense:read")
def check_budget_available_mcp(db, *, dept: str, category: str,
                                amount: Decimal,
                                period: str | None = None) -> dict:
    """MCP 工具：检查预算是否可承担金额（v1.1 §5.2 预算服务MCP）。

    scope: expense:read（P0-2 裁决——不新增 budget:read）。
    """
    r = check_available(db, tenant_id=_DEFAULT_TENANT, dept=dept,
                        category=category, period=period or _current_period(),
                        amount=Decimal(str(amount)))
    return {
        "available": r.available,
        "remaining": str(r.remaining),
        "overage": str(r.overage),
    }