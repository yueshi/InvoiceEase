# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
from datetime import date
from typing import Any

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Invoice, Mailbox, Role, User
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut
from invoicing.workflow import services


def _mcp_admin_user() -> User:
    """MCP 静态 token 即管理员通道：合成 admin 用户走 service 层（无租户过滤，MVP 单租户）。"""
    return User(id=0, username="mcp", password_hash="", role=Role.admin.value)


def fetch_invoices(mailbox_id: int | None = None) -> PollResultOut:
    total = {"received": 0, "rejected_images": 0, "ignored": 0, "duplicates": 0, "errors": 0}
    with SessionLocal() as db:
        q = db.query(Mailbox).filter(Mailbox.enabled.is_(True))
        if mailbox_id is not None:
            q = q.filter(Mailbox.id == mailbox_id)
        mailboxes = q.all()
        if mailbox_id is not None and not mailboxes:
            raise ValueError(f"邮箱不存在或已停用: {mailbox_id}")
        for mb in mailboxes:
            result = poll_mailbox(db, mb)
            for key in total:
                total[key] += getattr(result, key)
        write_audit(
            db, action="FETCH", channel="mcp",
            detail={"mailbox_ids": [mb.id for mb in mailboxes], "result": total},
        )
        db.commit()
    return PollResultOut(**total)


def list_invoices_mcp(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    with SessionLocal() as db:
        return services.list_invoices(
            db, _mcp_admin_user(), status, date_from, date_to, keyword, page, page_size
        )


def get_invoice_mcp(invoice_id: int) -> InvoiceOut:
    with SessionLocal() as db:
        return services.get_invoice(db, _mcp_admin_user(), invoice_id)
