# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
import re
from datetime import date

from fastapi import HTTPException

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import AuditAction, CompanyInfo, CompanyKind, Mailbox, Role, User
from invoicing.schemas.company_info import CompanyInfoOut, TAX_ID_PATTERN
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
    from invoicing.mcp.extract import OCR_UNAVAILABLE, _read_file
    from invoicing.models import AuditAction, Invoice, InvoiceStatus
    from invoicing.models.enums import FileType
    from invoicing.storage import get_storage
    from invoicing.workers.queue import enqueue_parse_sync

    path = Path(file_path)
    data = _read_file(file_path)
    kind = classify_attachment(path.name, "", data)
    if kind == "IMAGE":
        from invoicing.parse.ocr import get_ocr_provider

        if get_ocr_provider() is None:
            raise ValueError(OCR_UNAVAILABLE)
        # 图片原件合规归档（本地工具语义；邮箱收取仍拒收图片）
        kind = FileType.IMAGE.value
    if kind not in ("PDF", "OFD", "XML", "IMAGE"):
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


def company_info_list(kind: str | None = None) -> list[CompanyInfoOut]:
    """常用公司列表（kind 可选：self/supplier/other）。"""
    with SessionLocal() as db:
        q = db.query(CompanyInfo)
        if kind:
            q = q.filter(CompanyInfo.kind == kind)
        return [CompanyInfoOut.model_validate(i, from_attributes=True) for i in q.order_by(CompanyInfo.id).all()]


def company_info_save(
    name: str,
    tax_id: str,
    kind: str = CompanyKind.other.value,
    is_default: bool = False,
    remark: str | None = None,
) -> CompanyInfoOut:
    """保存常用公司（同税号更新；is_default 仅 kind=self，设默认清其他默认）。

    校验与 REST 对齐（tax_id 18 位 / kind 枚举 / name 非空 / 默认联动），非法输入抛 ValueError。
    """
    if not name or not name.strip():
        raise ValueError("公司名称不能为空")
    if not re.fullmatch(TAX_ID_PATTERN, tax_id):
        raise ValueError("税号必须为 18 位字母数字（[0-9A-Z]）")
    if kind not in {k.value for k in CompanyKind}:
        raise ValueError(f"非法类型: {kind}（可选 self/supplier/other）")
    if is_default and kind != CompanyKind.self.value:
        raise ValueError("is_default 仅适用于 kind=self")
    with SessionLocal() as db:
        info = db.query(CompanyInfo).filter(CompanyInfo.tax_id == tax_id).first()
        if info is None:
            info = CompanyInfo(tax_id=tax_id)
            db.add(info)
        # kind 改为非 self 时 is_default 强制 False，防脏数据（K1 修复轮教训）
        if kind != CompanyKind.self.value:
            is_default = False
        if is_default:
            db.query(CompanyInfo).filter(CompanyInfo.is_default.is_(True)).update({"is_default": False})
        info.name = name.strip()
        info.kind = kind
        info.is_default = is_default
        info.remark = remark
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "company_info", "tax_id": tax_id},
        )
        db.commit()
        return CompanyInfoOut.model_validate(info, from_attributes=True)


def company_info_delete(id: int) -> dict:
    """删除常用公司；不存在抛 ValueError。"""
    with SessionLocal() as db:
        info = db.get(CompanyInfo, id)
        if info is None:
            raise ValueError(f"记录不存在: {id}")
        db.delete(info)
        write_audit(
            db, action=AuditAction.CONFIG_CHANGE.value, channel="mcp",
            detail={"entity": "company_info", "id": id, "deleted": True},
        )
        db.commit()
        return {"ok": True}
