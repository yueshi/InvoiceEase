from datetime import datetime, time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import Invoice, User
from invoicing.models.fields import utcnow
from invoicing.schemas.stats import StatsOverviewOut
from invoicing.security import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverviewOut)
def overview(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    today = utcnow().date()  # naive-UTC，与库内时间戳一致
    today_start = datetime.combine(today, time.min)
    month_start = datetime.combine(today.replace(day=1), time.min)
    return StatsOverviewOut(
        pending_review=db.query(Invoice).filter(Invoice.status == "pending_review").count(),
        pending_submit=db.query(Invoice).filter(Invoice.status == "pending_submit").count(),
        today_new=db.query(Invoice).filter(Invoice.created_at >= today_start).count(),
        month_total=db.query(Invoice).filter(Invoice.created_at >= month_start).count(),
    )
