"""MCP 工具测试：直接函数级 + in-memory Client 协议级。"""
from datetime import date
from decimal import Decimal

import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp.server import mcp
from invoicing.mcp.tools import fetch_invoices, get_invoice_mcp, list_invoices_mcp
from invoicing.models import AuditLog, Invoice, Mailbox, Role, User
from invoicing.schemas.invoice import InvoiceOut
from invoicing.security import hash_password


def _seed(db):
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role="finance_staff"))
    db.add(
        Invoice(
            file_url="a.xml", file_type="XML", invoice_number="24312000000012345678",
            status="pending_submit", total_amount=Decimal("1000.00"),
            seller_name="示例科技有限公司", issue_date=date(2026, 8, 1),
            parse_source="XML", confidence_score=1.0, verify_status="passed",
        )
    )
    db.flush()
    # 注意：MCP 工具各自开启独立 SessionLocal 会话，种子数据必须 commit 才可见；
    # 且仅 flush 会让 fixture 会话持有写锁，导致后续 Mailbox 写入报 database is locked。
    db.commit()


def _mcp_admin(db):
    """工具层改为从认证上下文取真实用户（不再合成 admin）。"""
    admin = User(username="mcp_admin", password_hash="x", role=Role.admin.value)
    db.add(admin)
    db.commit()
    return admin


def test_list_invoices_mcp(db, mcp_auth):
    _seed(db)
    mcp_auth(_mcp_admin(db))
    result = list_invoices_mcp(page=1, page_size=20)
    assert result.total == 1
    assert result.items[0].invoice_number == "24312000000012345678"
    assert isinstance(result.items[0], InvoiceOut)


def test_get_invoice_mcp(db, mcp_auth):
    _seed(db)
    mcp_auth(_mcp_admin(db))
    with SessionLocal() as s:
        inv_id = s.query(Invoice).first().id
    result = get_invoice_mcp(invoice_id=inv_id)
    assert result.invoice_number == "24312000000012345678"
    assert isinstance(result, InvoiceOut)


def test_fetch_invoices_mcp_writes_mcp_audit(db, monkeypatch, mcp_admin_auth):
    from invoicing.fetch.service import PollResult

    _seed(db)
    with SessionLocal() as s:
        s.add(Mailbox(
            name="mcp-mailbox", imap_host="127.0.0.1", imap_port=1, use_ssl=False,
            username="u@x.com", password_encrypted="enc:x",
        ))
        s.commit()

    def fake_poll(db2, mailbox, fetcher=None):
        return PollResult(received=1, rejected_images=0, ignored=0, duplicates=0, errors=0)

    monkeypatch.setattr("invoicing.mcp.tools.poll_mailbox", fake_poll)
    result = fetch_invoices(mailbox_id=None)
    assert result.received == 1
    with SessionLocal() as s:
        logs = s.query(AuditLog).filter(AuditLog.action == "FETCH", AuditLog.channel == "mcp").all()
    assert len(logs) == 1


def test_receipt_report_excludes_invoice_exempt(db, mcp_admin_auth):
    """催票清单不得含无需发票行（税费类）——WorkBuddy 侧的面孔，免误催。"""
    from invoicing.mcp.tools import receipt_report
    from invoicing.models import BankReceipt

    _seed(db)
    db.add_all([
        BankReceipt(file_url="t.pdf", file_type="PDF", trade_date=date(2026, 8, 7),
                    counterparty_name="国家金库陕西省西咸新区支库", amount=Decimal("1116.00"),
                    status="unmatched", direction="付", category="tax"),
        BankReceipt(file_url="b.pdf", file_type="PDF", trade_date=date(2026, 8, 8),
                    counterparty_name="供应商甲", amount=Decimal("800.00"),
                    status="unmatched", direction="付", category="purchase"),
    ])
    db.commit()

    text = receipt_report(month="2026-08")
    assert "无票支出 1 笔" in text
    assert "供应商甲" in text
    assert "国家金库" not in text


def test_receipt_list_exposes_category_and_requirement(db, mcp_admin_auth):
    """MCP 清单带交易性质与发票要求（终审 7）：Agent 侧不能只靠 status 猜「要不要票」。"""
    from invoicing.mcp.tools import receipt_list
    from invoicing.models import BankReceipt

    _seed(db)
    db.add_all([
        BankReceipt(file_url="t.pdf", file_type="PDF", trade_date=date(2026, 8, 7),
                    counterparty_name="国家金库陕西省西咸新区支库", amount=Decimal("1116.00"),
                    status="unmatched", direction="付", category="tax"),
        BankReceipt(file_url="b.pdf", file_type="PDF", trade_date=date(2026, 8, 8),
                    counterparty_name="供应商甲", amount=Decimal("800.00"),
                    status="unmatched", direction="付", category="purchase"),
    ])
    db.commit()

    by_name = {r["counterparty_name"]: r for r in receipt_list(month="2026-08")}
    tax, purchase = by_name["国家金库陕西省西咸新区支库"], by_name["供应商甲"]
    assert (tax["category"], tax["invoice_requirement"]) == ("tax", "none")   # 无需发票
    assert (purchase["category"], purchase["invoice_requirement"]) == ("purchase", "fetch")
    # 既有字段不得改动（WorkBuddy 契约）
    assert {"id", "trade_date", "amount", "direction", "status", "paired_invoice_id"} <= set(purchase)


@pytest.mark.asyncio
async def test_in_memory_client_lists_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"invoice_fetch", "invoice_list", "invoice_detail"} <= names
