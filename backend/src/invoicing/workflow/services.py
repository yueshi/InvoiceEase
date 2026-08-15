# workflow/services.py
from sqlalchemy import or_
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import Invoice, InvoiceStatus, Role, User
from invoicing.models.fields import utcnow
from invoicing.schemas.invoice import InvoiceListResponse
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
