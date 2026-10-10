"""MCP budget 工具测试（v1.1 §5.2 预算服务MCP）。"""
from decimal import Decimal

import pytest

from invoicing.models.budget import Budget
from invoicing.workflow.budget_service import (
    query_budget_mcp, check_budget_available_mcp,
)


@pytest.fixture(autouse=True)
def _mcp_ctx(mcp_admin_auth):
    """工具体带 @requires（P0-2 补），直调需要认证上下文。"""


def test_query_budget_mcp_basic(db):
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("5000")))
    db.commit()
    v = query_budget_mcp(db, dept="eng", category="travel", period="2026-10")
    # Decimal str() 保留两位小数 → "5000.00"
    assert Decimal(v["allocated"]) == Decimal("5000")
    assert Decimal(v["used"]) == Decimal("0")
    assert Decimal(v["available"]) == Decimal("5000")


def test_query_budget_mcp_no_data(db):
    v = query_budget_mcp(db, dept="eng", category="travel", period="2026-10")
    assert v["allocated"] == "0"
    assert v["available"] == "0"


def test_check_budget_available_within(db):
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("5000")))
    db.commit()
    r = check_budget_available_mcp(db, dept="eng", category="travel",
                                    amount=Decimal("3000"), period="2026-10")
    assert r["available"] is True
    assert Decimal(r["remaining"]) == Decimal("5000")
    assert Decimal(r["overage"]) == Decimal("0")


def test_check_budget_available_over(db):
    db.add(Budget(tenant_id="default", dept="eng", category="travel",
                  period="2026-10", amount=Decimal("1000")))
    db.commit()
    r = check_budget_available_mcp(db, dept="eng", category="travel",
                                    amount=Decimal("3000"), period="2026-10")
    assert r["available"] is False
    assert Decimal(r["overage"]) == Decimal("2000")


def test_check_budget_available_period_default(db):
    """period 不传时用当月（不会崩）。"""
    r = check_budget_available_mcp(db, dept="eng", category="travel",
                                    amount=Decimal("100"))
    # 不报错；无预算配置 → available=False（allocated=0, 100 超支）
    assert r["available"] is False
    assert Decimal(r["overage"]) == Decimal("100")