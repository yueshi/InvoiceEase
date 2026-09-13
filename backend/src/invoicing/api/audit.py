from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import AuditLog, User
from invoicing.schemas.audit import AuditListResponse
from invoicing.security import require_role

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditListResponse)
def list_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    invoice_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    q = db.query(AuditLog)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    else:
        # 历史 LOGIN 行默认隐藏（登录已不再记录；显式 action=LOGIN 仍可回溯）
        q = q.filter(AuditLog.action != "LOGIN")
    if invoice_id:
        q = q.filter(AuditLog.invoice_id == invoice_id)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return AuditListResponse(items=items, total=total, page=page, page_size=page_size)
