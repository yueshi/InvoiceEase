"""成本报表测试。"""
from datetime import date
from decimal import Decimal

from invoicing.models import Invoice
from invoicing.reports import export_monthly_excel, monthly_cost

VALID_STATUS = ("parsed", "pending_review", "verifying", "pending_submit", "submitted", "archived")


def _seed(db, month="2026-08"):
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345678",
        status="pending_submit", total_amount=Decimal("1000.00"),
        amount_without_tax=Decimal("943.40"), tax_amount=Decimal("56.60"),
        seller_name="高德打车科技有限公司", issue_date=date(2026, 8, 5),
        expense_type="travel", cost_center="市场部",
    )
    db.add(inv)
    inv2 = Invoice(
        file_url="b.xml", file_type="XML", invoice_number="24312000000012345679",
        status="rejected", total_amount=Decimal("500.00"),  # 驳回票不计入
        seller_name="某公司", issue_date=date(2026, 8, 6), expense_type="office",
    )
    db.add(inv2)
    db.flush()
    return inv


def test_monthly_cost_aggregates(db):
    _seed(db)
    result = monthly_cost(db, "2026-08")
    assert result["total_count"] == 1  # rejected 不计入
    assert result["total_amount"] == Decimal("1000.00")
    assert result["by_type"]["travel"] == Decimal("1000.00")


def test_monthly_cost_dual_amount_gauge(db):
    """双金额口径：价税合计/不含税/税额分别汇总（一般纳税人与小规模成本口径差异）。"""
    _seed(db)
    result = monthly_cost(db, "2026-08")
    assert result["total_amount"] == Decimal("1000.00")
    assert result["total_without_tax"] == Decimal("943.40")
    assert result["total_tax"] == Decimal("56.60")
    assert result["rows"][0]["amount_without_tax"] == "943.40"


def test_monthly_cost_tenant_isolation(db):
    """tenant 隔离：仅统计本租户发票（P4 代账多客户前堵住的口径漏洞）。"""
    _seed(db)
    inv = Invoice(
        file_url="d.xml", file_type="XML", invoice_number="24312000000012345681",
        status="pending_submit", total_amount=Decimal("300.00"),
        seller_name="别家租户公司", issue_date=date(2026, 8, 6), tenant_id="other",
    )
    db.add(inv)
    db.flush()
    assert monthly_cost(db, "2026-08")["total_count"] == 1
    assert monthly_cost(db, "2026-08", tenant_id="other")["total_count"] == 1


def test_monthly_cost_excludes_other_months(db):
    _seed(db)
    inv = Invoice(
        file_url="c.xml", file_type="XML", invoice_number="24312000000012345680",
        status="pending_submit", total_amount=Decimal("300.00"),
        seller_name="某公司", issue_date=date(2026, 7, 31),
        expense_type="other",
    )
    db.add(inv)
    db.flush()
    assert monthly_cost(db, "2026-08")["total_count"] == 1


def test_invalid_month_raises(db):
    import pytest

    _seed(db)
    with pytest.raises(ValueError):
        monthly_cost(db, "2026-13")


def test_export_excel_returns_xlsx(db):
    _seed(db)
    data = export_monthly_excel(db, "2026-08")
    assert data[:2] == b"PK"  # xlsx 是 zip 容器


def test_monthly_health_contains_key_sections(db):
    """R3：健康报告文本含收票/验真/成本/无票关键段。"""
    from invoicing.reports import monthly_health

    _seed(db)
    text = monthly_health(db, "2026-08")
    assert "收票" in text
    assert "成本" in text
    assert "无票支出" in text
    assert "1000.00" in text
