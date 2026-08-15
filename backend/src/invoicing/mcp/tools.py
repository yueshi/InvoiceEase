# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
from datetime import date

from fastapi import HTTPException

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Mailbox, Role, User
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
        result = services.list_invoices(
            db, _mcp_admin_user(), status, date_from, date_to, keyword, page, page_size
        )
    # MCP 返回需 pydantic 模型（REST 由 response_model 转换，MCP 无此层）
    result.items = [InvoiceOut.model_validate(item, from_attributes=True) for item in result.items]
    return result


def get_invoice_mcp(invoice_id: int) -> InvoiceOut:
    with SessionLocal() as db:
        try:
            inv = services.get_invoice(db, _mcp_admin_user(), invoice_id)
        except HTTPException as e:
            # service 层 404 泄漏到 MCP 层，映射为协议友好的错误信息
            raise ValueError(f"发票不存在或无权访问: {invoice_id}") from e
        return InvoiceOut.model_validate(inv, from_attributes=True)


def ingest_invoice(file_path: str) -> InvoiceOut:
    """WorkBuddy 归档闭环：原件入存储 → 解析 → 验真（本地模式内联）→ 返回发票记录。
    注意：本地模式返回终态 InvoiceOut；redis 模式返回 parsing 中间态（异步 worker 处理），状态以发票详情查询为准。"""
    from pathlib import Path
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.mcp.extract import IMAGE_REJECT
    from invoicing.models import AuditAction, Invoice, InvoiceStatus
    from invoicing.storage import get_storage
    from invoicing.workers.queue import enqueue_parse_sync

    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"文件不存在: {file_path}")
    data = path.read_bytes()
    kind = classify_attachment(path.name, "", data)
    if kind == "IMAGE":
        raise ValueError(IMAGE_REJECT)
    if kind not in ("PDF", "OFD", "XML"):
        raise ValueError(f"不支持的格式: {kind or '未知'}")

    key = f"tenant-default/workbuddy/{uuid4().hex}-{path.name}"
    get_storage().put(key, data, "application/octet-stream")

    with SessionLocal() as db:
        inv = Invoice(
            file_url=key,
            file_type=kind,
            status=InvoiceStatus.parsing.value,
            email_subject="WorkBuddy 导入",
        )
        db.add(inv)
        db.commit()
        invoice_id = inv.id
        write_audit(
            db, action=AuditAction.INGEST.value, invoice_id=invoice_id, channel="mcp",
            detail={"source_file": file_path, "file_type": kind},
        )
        db.commit()

    enqueue_parse_sync(invoice_id)  # 本地模式内联执行 parse+verify；redis 模式入队

    with SessionLocal() as db:
        inv = db.get(Invoice, invoice_id)
        return InvoiceOut.model_validate(inv, from_attributes=True)
