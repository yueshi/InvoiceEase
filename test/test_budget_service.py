"""budget_service 测试（P0-1 validate_expense 配套）。"""
from decimal import Decimal

from invoicing.models.budget import Budget
from invoicing.workflow.budget_service import query_budget


def test_query_budget_basic(db):
    db.add(Budget(tenant_id="t1", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("50000")))
    db.commit()
    v = query_budget(db, tenant_id="t1", dept="eng",
                     category="travel", period="2026-10")
    assert v.allocated == Decimal("50000")
    # ponytail: used=0 ceiling — 需 ExpenseClaim.dept/expense_type 字段后启用 used 计算
    assert v.used == Decimal("0")
    assert v.available == Decimal("50000")


def test_query_budget_no_data_returns_zero(db):
    v = query_budget(db, tenant_id="t1", dept="eng",
                     category="travel", period="2026-10")
    assert v.allocated == Decimal("0")
    assert v.used == Decimal("0")
    assert v.available == Decimal("0")


def test_query_budget_scoped_by_tenant(db):
    """不同 tenant 互不干扰。"""
    db.add(Budget(tenant_id="t1", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("1000")))
    db.add(Budget(tenant_id="t2", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("5000")))
    db.commit()
    v1 = query_budget(db, tenant_id="t1", dept="eng",
                      category="travel", period="2026-10")
    v2 = query_budget(db, tenant_id="t2", dept="eng",
                      category="travel", period="2026-10")
    assert v1.allocated == Decimal("1000")
    assert v2.allocated == Decimal("5000")