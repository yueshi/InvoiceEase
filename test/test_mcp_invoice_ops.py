"""MCP 发票更新/删除工具测试（直调函数 + in-memory 工具列表）。"""
from datetime import date
from decimal import Decimal

import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp.server import mcp
from invoicing.mcp.tools import invoice_delete, invoice_update
from invoicing.models import AuditLog, Invoice


def _seed_invoice(db) -> Invoice:
    inv = Invoice(
        file_url="mcp-test/a.xml",
        file_type="XML",
        invoice_number="24312000000012345678",
        status="pending_review",
        total_amount=Decimal("1000.00"),
        seller_name="原销售方",
        issue_date=date(2026, 8, 1),
        parse_source="XML",
        confidence_score=1.0,
    )
    db.add(inv)
    db.commit()  # MCP 工具用独立 SessionLocal，需提交可见
    return inv


def test_invoice_update_changes_fields_and_audits(db):
    inv = _seed_invoice(db)
    updated = invoice_update(
        invoice_id=inv.id,
        total_amount="999.99",
        seller_name="更正后的销售方",
        review_note="MCP 复核修正",
    )
    assert updated.total_amount == Decimal("999.99")
    assert updated.seller_name == "更正后的销售方"
    assert updated.review_note == "MCP 复核修正"
    assert updated.invoice_number == "24312000000012345678"  # 未传字段不变
    db.flush()
    logs = db.query(AuditLog).filter(AuditLog.action == "INVOICE_UPDATE").all()
    assert len(logs) == 1
    assert logs[0].channel == "mcp"
    assert logs[0].user_id is None
    assert "total_amount" in logs[0].detail["changed"]


def test_invoice_update_missing_raises(db):
    with pytest.raises(ValueError, match="发票不存在"):
        invoice_update(invoice_id=999999, total_amount="1.00")


def test_invoice_delete_removes_and_audits(db):
    inv = _seed_invoice(db)
    result = invoice_delete(invoice_id=inv.id)
    assert result == {"ok": True}
    with SessionLocal() as s:
        assert s.get(Invoice, inv.id) is None
        logs = s.query(AuditLog).filter(AuditLog.action == "INVOICE_DELETE").all()
        assert len(logs) == 1
        assert logs[0].channel == "mcp"
        assert logs[0].detail["snapshot"]["invoice_number"] == "24312000000012345678"


def test_invoice_delete_missing_raises(db):
    with pytest.raises(ValueError, match="发票不存在"):
        invoice_delete(invoice_id=999999)


@pytest.mark.asyncio
async def test_in_memory_client_lists_invoice_ops_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"invoice_update", "invoice_delete"} <= names
