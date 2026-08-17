# workflow/services.py
from sqlalchemy import or_
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import Invoice, InvoiceStatus, Role, User
from invoicing.models.fields import utcnow
from invoicing.schemas.invoice import InvoiceListResponse
from invoicing.storage import get_storage
from invoicing.workflow.state import transition
from invoicing.workers.queue import enqueue_verify_sync


def _scope_query(db: Session, current_user: User):
    q = db.query(Invoice)
    if current_user.role == Role.employee.value:
        q = q.filter(Invoice.user_id == current_user.id)
    return q


def list_invoices(
    db: Session,
    current_user: User,
    status: str | None = None,
    date_from=None,
    date_to=None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    q = _scope_query(db, current_user)
    if status:
        q = q.filter(Invoice.status == status)
    if date_from:
        q = q.filter(Invoice.issue_date >= date_from)
    if date_to:
        q = q.filter(Invoice.issue_date <= date_to)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            or_(
                Invoice.invoice_number.ilike(like),
                Invoice.seller_name.ilike(like),
                Invoice.buyer_name.ilike(like),
            )
        )
    total = q.count()
    items = q.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return InvoiceListResponse(items=items, total=total, page=page, page_size=page_size)


def get_invoice(db: Session, current_user: User, invoice_id: int) -> Invoice:
    inv = _scope_query(db, current_user).filter(Invoice.id == invoice_id).first()
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    return inv


def review_invoice(db: Session, current_user: User, invoice_id: int, action: str, note: str | None) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    if inv.status != InvoiceStatus.pending_review.value:
        from fastapi import HTTPException

        raise HTTPException(409, "仅待复核状态的发票可复核")
    to_status = InvoiceStatus.pending_submit.value if action == "approve" else InvoiceStatus.rejected.value
    transition(inv, to_status)
    inv.review_note = note
    inv.reviewed_by = current_user.id
    inv.reviewed_at = utcnow()
    write_audit(
        db, action="REVIEW", user_id=current_user.id, invoice_id=inv.id, channel="web",
        detail={"action": action, "note": note},
    )
    db.commit()
    return inv


def re_verify_invoice(db: Session, current_user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        from fastapi import HTTPException

        raise HTTPException(404, "发票不存在")
    if inv.status not in (
        InvoiceStatus.parsed.value,
        InvoiceStatus.pending_review.value,
        InvoiceStatus.pending_submit.value,
    ):
        from fastapi import HTTPException

        raise HTTPException(409, "当前状态不可重新验真")
    transition(inv, InvoiceStatus.verifying.value)
    inv.verify_status = "pending"
    write_audit(
        db, action="REVERIFY", user_id=current_user.id, invoice_id=inv.id, channel="web"
    )
    db.commit()
    enqueue_verify_sync(inv.id)
    # 本地队列模式内联完成验真：刷新会话后再返回，响应反映验真后状态（redis 模式为尽力读取当前值）
    db.refresh(inv)
    return inv


_SNAPSHOT_COLS = (
    "invoice_code", "invoice_number", "issue_date", "amount_without_tax", "tax_amount",
    "total_amount", "total_amount_cn", "seller_name", "seller_tax_id", "buyer_name",
    "buyer_tax_id", "invoice_type", "file_url", "file_type", "xml_url", "parse_source",
    "confidence_score", "verify_status", "status", "email_message_id",
)


_AMOUNT_FIELDS = ("amount_without_tax", "tax_amount", "total_amount", "total_amount_cn")


def _revalidate_invoice(inv: Invoice) -> None:
    """金额字段变更后重算 validation_errors：用户修正值参与校验，覆盖源文件提取告警。"""
    from invoicing.parse.schemas import ParsedInvoice
    from invoicing.parse.validation import validate

    if (
        inv.invoice_number is None
        or inv.issue_date is None
        or inv.amount_without_tax is None
        or inv.tax_amount is None
        or inv.total_amount is None
    ):
        return  # 字段不全无法校验，保留既有错误
    parsed = ParsedInvoice(
        invoice_number=inv.invoice_number,
        issue_date=inv.issue_date,
        amount_without_tax=inv.amount_without_tax,
        tax_amount=inv.tax_amount,
        total_amount=inv.total_amount,
        total_amount_cn=inv.total_amount_cn or "",
        seller_name=inv.seller_name or "",
        seller_tax_id=inv.seller_tax_id or "",
        buyer_name=inv.buyer_name or "",
        buyer_tax_id=inv.buyer_tax_id or "",
        confidence_score=inv.confidence_score or 0.0,
        parse_source=inv.parse_source or "",
    )
    errs = validate(parsed)
    inv.validation_errors = [e.model_dump() for e in errs] if errs else None


def update_invoice(db: Session, current_user: User | None, invoice_id: int, data: dict) -> Invoice:
    """更新发票业务字段（人工复核纠正）；状态变更走 review/verify 专用端点。

    current_user 可为 None（MCP 通道无用户上下文，审计 user_id 留空）。"""
    from fastapi import HTTPException

    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "发票不存在")
    changed: dict = {}
    for field, value in data.items():
        if value is None:
            continue  # 显式 null 不落库（str 清空用空串表达，防 NOT NULL 字段崩）
        old = getattr(inv, field)
        if old != value:
            setattr(inv, field, value)
            changed[field] = str(value)
    if any(f in changed for f in _AMOUNT_FIELDS):
        _revalidate_invoice(inv)  # 用户修正金额/大写 → 校验告警重算
    # B4：关键字段变更 → 既有 AI 预判基于旧数据作废（理由不得基于旧数据），班表下轮重算
    _REVIEW_SENSITIVE_FIELDS = set(_AMOUNT_FIELDS) | {
        "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id", "invoice_type", "issue_date",
    }
    if changed and any(f in _REVIEW_SENSITIVE_FIELDS for f in changed):
        inv.ai_review_verdict = None
        inv.ai_review_reason = None
        inv.ai_review_confidence = None
        inv.ai_reviewed_at = None
    if changed:
        write_audit(
            db, action="INVOICE_UPDATE", user_id=current_user.id if current_user else None,
            invoice_id=inv.id, channel="web" if current_user else "mcp",
            detail={"changed": changed},
        )
    db.commit()
    return inv




def unblock_invoice(db: Session, current_user: User | None, invoice_id: int) -> Invoice:
    """人工放行：blocked → 待复核（清除重复标记与悬空引用）。

    current_user 可为 None（MCP 通道无用户上下文）。
    """
    from fastapi import HTTPException

    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "发票不存在")
    if inv.status != InvoiceStatus.blocked.value:
        raise HTTPException(409, "仅已拦截（blocked）状态的发票可放行")
    inv.duplicate_flag = False
    inv.duplicate_of_id = None
    transition(inv, InvoiceStatus.pending_review.value)
    write_audit(
        db, action="UNBLOCK", user_id=current_user.id if current_user else None,
        invoice_id=inv.id, channel="web" if current_user else "mcp",
        detail={"from": "blocked", "to": "pending_review"},
    )
    db.commit()
    return inv

def delete_invoice(db: Session, current_user: User | None, invoice_id: int) -> dict:
    """删除发票：先写全字段快照审计（合规留痕），再删原件与记录。"""
    import logging

    from fastapi import HTTPException

    logger = logging.getLogger(__name__)
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "发票不存在")
    snapshot = {col: str(getattr(inv, col)) for col in _SNAPSHOT_COLS}
    write_audit(
        db, action="INVOICE_DELETE", user_id=current_user.id if current_user else None,
        invoice_id=inv.id, channel="web" if current_user else "mcp",
        detail={"snapshot": snapshot},
    )
    # 悬空引用清理：指向本记录的重复标记清除；被拦截的依赖票转待复核（人工重新判断）
    dependents = db.query(Invoice).filter(Invoice.duplicate_of_id == invoice_id).all()
    for dep in dependents:
        dep.duplicate_flag = False
        dep.duplicate_of_id = None
        if dep.status == InvoiceStatus.blocked.value:
            transition(dep, InvoiceStatus.pending_review.value)
    storage = get_storage()
    for key in (inv.file_url, inv.xml_url):
        if not key:
            continue
        try:
            storage.delete(key)
        except Exception:  # 原件删除失败不阻塞记录删除（审计已留痕，文件残留可清理）
            logger.warning("原件删除失败 invoice_id=%s key=%s", invoice_id, key, exc_info=True)
    db.delete(inv)
    db.commit()
    return {"ok": True}
