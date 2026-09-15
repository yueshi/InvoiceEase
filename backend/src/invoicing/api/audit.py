from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import AuditLog, User
from invoicing.schemas.audit import AuditListResponse
from invoicing.security import require_role

router = APIRouter(prefix="/audit-logs", tags=["audit"])


# 身份与账号安全事件：登录成败/登出 + 密码与账号权限变更（谁改了谁的密码/权限/状态）
_IDENTITY_ACTIONS = (
    "LOGIN", "LOGIN_FAILED", "LOGOUT",
    "PASSWORD_CHANGE", "PASSWORD_RESET",
    "USER_SUSPEND", "USER_RESUME", "USER_ROLE_CHANGE", "USER_CREATE",
)


@router.get("", response_model=AuditListResponse)
def list_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    category: str = "business",
    outcome: str | None = None,
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
        # 显式 action 优先（历史回溯不受分类限制）
        q = q.filter(AuditLog.action == action)
    elif category == "security":
        # 安全审计：身份与账号安全事件（登录成败/登出/密码与权限变更）
        q = q.filter(AuditLog.action.in_(_IDENTITY_ACTIONS))
    elif category == "all":
        pass
    else:
        # 业务审计（默认）：排除身份事件，避免稀释日常查阅
        q = q.filter(~AuditLog.action.in_(_IDENTITY_ACTIONS))
    if outcome:
        # abnormal = 需要关注的异常（拦截/失败/系统错误），供「仅看异常」筛选
        if outcome == "abnormal":
            q = q.filter(AuditLog.outcome.in_(("blocked", "failed", "error")))
        elif outcome == "none":
            q = q.filter(AuditLog.outcome.is_(None))
        else:
            q = q.filter(AuditLog.outcome == outcome)
    if invoice_id:
        q = q.filter(AuditLog.invoice_id == invoice_id)
    if date_from:
        q = q.filter(AuditLog.created_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.created_at <= date_to)
    total = q.count()
    items = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return AuditListResponse(items=items, total=total, page=page, page_size=page_size)
