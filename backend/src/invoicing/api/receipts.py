"""银行回单 API（数字员工 P3/R1）：上传/列表/配对/凭证草稿导出。"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import BankReceipt, User
from invoicing.reports import receipts_to_csv
from invoicing.security import get_current_user, require_role

router = APIRouter(prefix="/receipts", tags=["receipts"])

_FINANCE = ("finance_staff", "finance_manager", "admin")

_MONTH_PATTERN = r"^\d{4}-\d{2}$"


def _receipt_out(r: BankReceipt) -> dict:
    return {
        "id": r.id,
        "file_url": r.file_url,
        "file_type": r.file_type,
        "trade_date": str(r.trade_date) if r.trade_date else None,
        "counterparty_name": r.counterparty_name,
        "amount": f"{r.amount:.2f}" if r.amount is not None else None,
        "abstract": r.abstract,
        "paired_invoice_id": r.paired_invoice_id,
        "status": r.status,
        "created_at": r.created_at,
    }


@router.post("/upload")
def upload_receipt(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(*_FINANCE)),
):
    """回单上传（PDF/图片）：存档 → 解析（PDF 文本层/图片 OCR/LLM 兜底）→ 自动配对建议。"""
    from uuid import uuid4

    from invoicing.fetch.filters import classify_attachment
    from invoicing.parse.receipt import parse_receipt_bytes, suggest_pair
    from invoicing.storage import get_storage

    data = file.file.read()
    kind = classify_attachment(file.filename or "receipt", "", data)
    if kind not in ("PDF", "IMAGE"):
        raise HTTPException(422, "仅支持 PDF 或图片格式的回单")

    fields = parse_receipt_bytes(data, kind)

    key = f"tenant-default/receipts/{uuid4().hex}-{file.filename or 'receipt'}"
    get_storage().put(key, data, "application/octet-stream")
    r = BankReceipt(
        file_url=key,
        file_type=kind,
        user_id=user.id,
        trade_date=fields.get("trade_date"),
        counterparty_name=fields.get("counterparty_name"),
        amount=fields.get("amount"),
        abstract=fields.get("abstract"),
        status="pending",
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    # 自动配对建议（D5）：金额相等+户名规范化；命中即落库，未命中标 unmatched
    from invoicing.parse.receipt import suggest_pair

    suggested = suggest_pair(db, r.id)
    if suggested is not None:
        r.paired_invoice_id = suggested
        r.status = "paired"
    else:
        r.status = "unmatched"
    db.commit()
    return _receipt_out(r)


@router.get("")
def list_receipts(
    month: str = Query(pattern=_MONTH_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    from invoicing.reports import _month_bounds

    start, end = _month_bounds(month)
    rows = (
        db.query(BankReceipt)
        .filter(BankReceipt.trade_date >= start, BankReceipt.trade_date < end)
        .order_by(BankReceipt.trade_date, BankReceipt.id)
        .all()
    )
    return [_receipt_out(r) for r in rows]


@router.get("/unmatched")
def unmatched_receipts(
    month: str = Query(pattern=_MONTH_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """无票费用提示（R2）：未配对回单清单。"""
    from invoicing.reports import _month_bounds

    start, end = _month_bounds(month)
    rows = (
        db.query(BankReceipt)
        .filter(
            BankReceipt.trade_date >= start,
            BankReceipt.trade_date < end,
            BankReceipt.paired_invoice_id.is_(None),
        )
        .order_by(BankReceipt.trade_date, BankReceipt.id)
        .all()
    )
    return [_receipt_out(r) for r in rows]


@router.post("/{receipt_id}/pair")
def pair_receipt(
    receipt_id: int,
    invoice_id: int = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """手动配对（覆盖建议）。"""
    from invoicing.models import Invoice

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "回单不存在")
    if db.get(Invoice, invoice_id) is None:
        raise HTTPException(404, "发票不存在")
    r.paired_invoice_id = invoice_id
    r.status = "paired"
    db.commit()
    return _receipt_out(r)


@router.post("/{receipt_id}/auto-pair")
def auto_pair_receipt(
    receipt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """按配对规则（D5：金额相等+户名规范化）自动配对建议并落库。"""
    from invoicing.parse.receipt import suggest_pair

    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "回单不存在")
    suggested = suggest_pair(db, receipt_id)
    if suggested is None:
        r.status = "unmatched"
        db.commit()
        return _receipt_out(r)
    r.paired_invoice_id = suggested
    r.status = "paired"
    db.commit()
    return _receipt_out(r)


@router.get("/export")
def export_receipts(
    month: str = Query(pattern=_MONTH_PATTERN),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*_FINANCE)),
):
    """凭证草稿 CSV（金蝶/用友通用列）。"""
    data = receipts_to_csv(db, month)
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="receipts-{month}.csv"'},
    )
