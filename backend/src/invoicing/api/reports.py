"""成本报表 API（数字员工 P1）。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import User
from invoicing.reports import export_monthly_excel, monthly_cost
from invoicing.security import require_role

router = APIRouter(prefix="/reports", tags=["reports"])

_MONTH_PATTERN = r"^\d{4}-\d{2}$"


@router.get("/monthly")
def get_monthly(
    month: str = Query(pattern=_MONTH_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("finance_staff", "finance_manager", "admin")),
):
    try:
        return monthly_cost(db, month)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/monthly/export")
def export_monthly(
    month: str = Query(pattern=_MONTH_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("finance_staff", "finance_manager", "admin")),
):
    try:
        data = export_monthly_excel(db, month)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="cost-{month}.xlsx"'},
    )
