"""MCP 发票更新/删除工具测试（直调函数 + in-memory 工具列表）。

P0-2 两段握手：写操作走 *_proposal（拿 token）→ confirm_execute（human_ack=true）
才落库；审计与副作用在确认阶段。
"""
from datetime import date
from decimal import Decimal

import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp import tools as mt
from invoicing.mcp.server import mcp
from invoicing.models import AuditLog, Invoice


@pytest.fixture(autouse=True)
def _admin_mcp_ctx(mcp_admin_auth):
    """本文件测工具**行为**（非身份）：默认以管理员身份调用，等价升级前的单令牌通道。
    身份/权限相关的回归见 test_mcp_permissions.py。"""


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


def test_invoice_update_changes_fields_and_audits(db, mcp_admin_auth):
    inv = _seed_invoice(db)
    prop = mt.invoice_update_proposal(
        invoice_id=inv.id,
        total_amount="999.99",
        seller_name="更正后的销售方",
        review_note="MCP 复核修正",
    )
    # 提案阶段零副作用
    db.flush()
    assert db.query(AuditLog).filter(AuditLog.action == "INVOICE_UPDATE").count() == 0

    updated = mt.confirm_execute(token=prop["proposal_token"],
                                 tool_name="invoice_update", human_ack=True)
    assert Decimal(updated["total_amount"]) == Decimal("999.99")
    assert updated["seller_name"] == "更正后的销售方"
    assert updated["review_note"] == "MCP 复核修正"
    assert updated["invoice_number"] == "24312000000012345678"  # 未传字段不变
    db.flush()
    logs = db.query(AuditLog).filter(AuditLog.action == "INVOICE_UPDATE").all()
    assert len(logs) == 1
    assert logs[0].channel == "mcp"
    # 2026-10-05 修订：MCP 已有身份体系，审计落到具体用户（此前恒 None 是旧残留）
    assert logs[0].user_id == mcp_admin_auth.id
    assert "total_amount" in logs[0].detail["changed"]


def test_invoice_update_proposal_preview_lists_changed_fields(db, mcp_admin_auth):
    inv = _seed_invoice(db)
    prop = mt.invoice_update_proposal(invoice_id=inv.id, seller_name="新名字")
    assert prop["preview"]["fields"] == {"seller_name": "新名字"}


def test_invoice_update_missing_raises(db):
    """不存在 → 确认阶段抛错（提案不算数）。"""
    prop = mt.invoice_update_proposal(invoice_id=999999, total_amount="1.00")
    with pytest.raises(ValueError, match="发票不存在"):
        mt.confirm_execute(token=prop["proposal_token"],
                           tool_name="invoice_update", human_ack=True)


def test_invoice_delete_removes_and_audits(db):
    inv = _seed_invoice(db)
    prop = mt.invoice_delete_proposal(invoice_id=inv.id)
    result = mt.confirm_execute(token=prop["proposal_token"],
                                tool_name="invoice_delete", human_ack=True)
    assert result == {"ok": True}
    with SessionLocal() as s:
        assert s.get(Invoice, inv.id) is None
        logs = s.query(AuditLog).filter(AuditLog.action == "INVOICE_DELETE").all()
        assert len(logs) == 1
        assert logs[0].channel == "mcp"
        assert logs[0].detail["snapshot"]["invoice_number"] == "24312000000012345678"


def test_invoice_delete_missing_raises(db):
    prop = mt.invoice_delete_proposal(invoice_id=999999)
    with pytest.raises(ValueError, match="发票不存在"):
        mt.confirm_execute(token=prop["proposal_token"],
                           tool_name="invoice_delete", human_ack=True)


@pytest.mark.asyncio
async def test_in_memory_client_lists_invoice_ops_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    # P0-2：写工具以 *_proposal 暴露（两段握手第一步）
    assert {"invoice_update_proposal", "invoice_delete_proposal"} <= names
    assert "confirm_execute" in names