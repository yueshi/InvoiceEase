from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import AuditLog, Invoice, User
from invoicing.models.fields import utcnow
from invoicing.schemas.stats import StatsOverviewOut, TrustStatsOut
from invoicing.security import get_current_user, require_role
from invoicing.workflow.services import scoped_invoices

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverviewOut)
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """工作台统计（FRD §3.5.2）：与发票列表同一 RBAC 范围——员工仅本人，财务/管理看全公司。"""
    today = utcnow().date()  # naive-UTC，与库内时间戳一致
    today_start = datetime.combine(today, time.min)
    month_start = datetime.combine(today.replace(day=1), time.min)
    q = scoped_invoices(db, user)
    return StatsOverviewOut(
        pending_review=q.filter(Invoice.status == "pending_review").count(),
        pending_submit=q.filter(Invoice.status == "pending_submit").count(),
        today_new=q.filter(Invoice.created_at >= today_start).count(),
        month_total=q.filter(Invoice.created_at >= month_start).count(),
    )


def trust_stats(db: Session, days: int = 7) -> dict:
    """信任仪表盘统计（M8）：近 N 天自动/人工复核数与改判率。

    改判定义：预判给出明确方向（approve/reject）而人工结果相反；uncertain
    与无预判的人工复核不计入改判分母。数据源 = B1 埋点（REVIEW 审计带
    ai_verdict/ai_confidence）与 AUTO_REVIEW 审计。
    """
    since = utcnow() - timedelta(days=days)
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_(("REVIEW", "AUTO_REVIEW")), AuditLog.created_at >= since)
        .all()
    )
    auto_count = sum(1 for l in logs if l.action == "AUTO_REVIEW")
    manual = [l for l in logs if l.action == "REVIEW"]
    directional = [
        l for l in manual
        if (l.detail or {}).get("ai_verdict") in ("approve", "reject")
    ]
    overturn_count = sum(
        1
        for l in directional
        if (l.detail or {}).get("ai_verdict") != (l.detail or {}).get("action")
    )
    overturn_rate = overturn_count / len(directional) if directional else 0.0
    return {
        "days": days,
        "auto_count": auto_count,
        "manual_count": len(manual),
        "overturn_count": overturn_count,
        "overturn_rate": round(overturn_rate, 4),
    }


@router.get("/trust", response_model=TrustStatsOut)
def trust(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("finance_staff", "finance_manager", "admin")),
):
    return trust_stats(db, days)
