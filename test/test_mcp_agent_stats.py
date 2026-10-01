"""invoice_stats 结构化统计工具：空库 / 有数据 / 非法月份 / MCP 真分发。"""
from datetime import date
from decimal import Decimal

import pytest

from invoicing.mcp import tools as mcp_tools
from invoicing.models import Invoice


def _seed_invoice(db, number: str, amount: str, expense_type: str, day: int = 5, month: str = "2026-08"):
    """经 ORM 插入当月发票（工具各自开会话，必须 commit 才可见）。"""
    y, m = (int(x) for x in month.split("-"))
    db.add(Invoice(
        file_url=f"{number}.xml", file_type="XML", invoice_number=number,
        status="pending_submit", total_amount=Decimal(amount),
        seller_name="示例科技有限公司", issue_date=date(y, m, day),
        expense_type=expense_type,
    ))
    db.commit()


def test_stats_empty_db(db, mcp_admin_auth):
    """空库：7 个键齐全、计数为 0、分布为空。"""
    data = mcp_tools.invoice_stats("2026-08")
    assert set(data) == {
        "month", "total_count", "total_amount", "total_without_tax",
        "total_tax", "by_type", "by_center",
    }
    assert data["month"] == "2026-08"
    assert data["total_count"] == 0
    assert float(data["total_amount"]) == 0
    assert float(data["total_without_tax"]) == 0
    assert float(data["total_tax"]) == 0
    assert data["by_type"] == {}
    assert data["by_center"] == {}


def test_stats_with_data(db, mcp_admin_auth):
    """有数据：计数、类型/部门分布正确，金额为可转 float 的字符串。"""
    _seed_invoice(db, "24312000000000000001", "120.50", "travel")
    _seed_invoice(db, "24312000000000000002", "79.50", "office")
    _seed_invoice(db, "24312000000000000003", "30.00", "travel", day=6)

    data = mcp_tools.invoice_stats("2026-08")
    assert data["total_count"] == 3
    assert float(data["total_amount"]) == 230.00
    assert all(isinstance(v, str) for v in data["by_type"].values())
    assert float(data["by_type"]["travel"]) == 150.50
    assert float(data["by_type"]["office"]) == 79.50
    assert isinstance(data, dict)  # JSON 可序列化（金额已转 str）

    # 其他月份不受影响
    assert mcp_tools.invoice_stats("2026-09")["total_count"] == 0


def test_stats_invalid_month(db, mcp_admin_auth):
    """非法月份格式 → ValueError（不做静默兜底）。"""
    with pytest.raises(ValueError):
        mcp_tools.invoice_stats("2026/09")
    with pytest.raises(ValueError):
        mcp_tools.invoice_stats("")


async def test_call_tool_dispatch_registered(db, mcp_admin_auth):
    """经 mcp.call_tool 真分发：工具已注册、返回文本非空。"""
    from invoicing.mcp.server import mcp

    result = await mcp.call_tool("invoice_stats", {"month": "2026-08"})
    assert not getattr(result, "is_error", False)
    text = "".join(getattr(b, "text", "") for b in (result.content or []))
    if not text:
        text = str(getattr(result, "structuredContent", None))
    assert text.strip()
