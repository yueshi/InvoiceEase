# api/invoices.py
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import Role, User
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut, InvoiceUpdate, ReviewRequest
from invoicing.security import get_current_user, require_role
from invoicing.storage import get_storage
from invoicing.workflow import services

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _content_disposition(filename: str) -> str:
    """RFC 6266：ASCII 兜底文件名 + UTF-8 filename*（中文原件名不触发 latin-1 编码错误）。"""
    ascii_name = filename.encode("ascii", "ignore").decode().strip() or "download"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return services.list_invoices(db, user, status, date_from, date_to, keyword, page, page_size)


@router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice_detail(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return services.get_invoice(db, user, invoice_id)


@router.get("/{invoice_id}/file")
def download_file(
    invoice_id: int,
    kind: str = Query("file", pattern="^(file|xml)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 统一流式返回（本地/S3 两后端同路径）；presigned 直传优化留 Plan D 生产对齐
    inv = services.get_invoice(db, user, invoice_id)
    key = inv.xml_url if kind == "xml" else inv.file_url
    if kind == "xml" and not key:
        from fastapi import HTTPException

        raise HTTPException(404, "该发票无 XML 原件")
    data = get_storage().get(key)
    filename = key.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/{invoice_id}/review", response_model=InvoiceOut)
def review(
    invoice_id: int,
    body: ReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.review_invoice(db, user, invoice_id, body.action, body.note)


@router.post("/{invoice_id}/verify", response_model=InvoiceOut)
def re_verify(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.re_verify_invoice(db, user, invoice_id)


@router.put("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(
    invoice_id: int,
    body: InvoiceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_staff.value, Role.finance_manager.value, Role.admin.value)),
):
    return services.update_invoice(db, user, invoice_id, body.model_dump(exclude_unset=True))


@router.delete("/{invoice_id}")
def delete_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.finance_manager.value, Role.admin.value)),
):
    return services.delete_invoice(db, user, invoice_id)
