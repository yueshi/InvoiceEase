"""报销 API（P0）：建单/明细/提交/审批/驳回/撤回 + 可选发票池。"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import ExpenseItem, Invoice, User
from invoicing.schemas.invoice import MoneyStr
from invoicing.security import get_current_user
from invoicing.workflow import expenses as svc

router = APIRouter(prefix="/expenses", tags=["expenses"])


# ---- schema --------------------------------------------------------------


class ClaimCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    remark: str | None = Field(default=None, max_length=512)


class ItemAddInvoice(BaseModel):
    invoice_id: int
    expense_type: str = "other"
    note: str | None = Field(default=None, max_length=512)


class ItemAddReceipt(BaseModel):
    receipt_id: int
    voucher_type: str = "bank_receipt"
    expense_type: str = "other"
    note: str | None = Field(default=None, max_length=512)


class ItemAddVoucher(BaseModel):
    voucher_type: str
    amount: str  # 金额字符串（避免浮点误差）
    expense_type: str = "other"
    note: str | None = Field(default=None, max_length=512)
    payee_name: str | None = Field(default=None, max_length=128)
    payee_id_no: str | None = Field(default=None, max_length=32)
    attachment_url: str | None = Field(default=None, max_length=512)


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    claim_no: str
    applicant_id: int
    title: str
    total_amount: MoneyStr
    status: str
    approver_id: int | None
    submitted_at: datetime | None
    decided_at: datetime | None
    rejected_reason: str | None
    remark: str | None
    created_at: datetime
    item_count: int = 0


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    claim_id: int
    invoice_id: int | None
    receipt_id: int | None
    voucher_type: str
    amount: MoneyStr
    expense_type: str
    note: str | None
    payee_name: str | None
    payee_id_no: str | None
    deductible: bool
    deductible_note: str | None
    active: bool


class EligibleInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str | None
    issue_date: str | None
    seller_name: str | None
    total_amount: MoneyStr
    expense_type: str | None


def _claim_out(db: Session, claim) -> ClaimOut:
    out = ClaimOut.model_validate(claim, from_attributes=True)
    out.item_count = svc.item_count(db, claim.id)
    return out


# ---- 端点 -----------------------------------------------------------------


@router.get("", response_model=list[ClaimOut])
def list_claims(
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """报销单列表：员工看本人；财务/管理员看全部（可用 status 过滤）。"""
    rows = svc.list_claims(db, user, status)
    return [_claim_out(db, r) for r in rows]


@router.post("", response_model=ClaimOut)
def create_claim(
    body: ClaimCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    try:
        claim = svc.create_claim(db, user, body.title, body.remark)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return _claim_out(db, claim)


@router.get("/eligible-invoices", response_model=list[EligibleInvoiceOut])
def eligible_invoices(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """可选发票池：本人上传的 ∪ 公共池；已验真、未拦截、未占用。"""
    rows = svc.eligible_invoices(db, user)
    return [
        EligibleInvoiceOut(
            id=i.id, invoice_number=i.invoice_number,
            issue_date=str(i.issue_date) if i.issue_date else None,
            seller_name=i.seller_name, total_amount=i.total_amount,
            expense_type=i.expense_type,
        )
        for i in rows
    ]


@router.get("/{claim_id}")
def claim_detail(claim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        claim, items = svc.claim_detail(db, user, claim_id)
    except ValueError as e:
        raise HTTPException(404 if "不存在" in str(e) else 403, str(e)) from None
    return {
        "claim": _claim_out(db, claim),
        "items": [ItemOut.model_validate(i, from_attributes=True) for i in items],
    }


@router.post("/{claim_id}/invoices")
def add_invoice(
    claim_id: int, body: ItemAddInvoice, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        item = svc.add_invoice(db, user, claim_id, body.invoice_id, body.expense_type, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return ItemOut.model_validate(item, from_attributes=True)


@router.post("/{claim_id}/receipts")
def add_receipt(
    claim_id: int, body: ItemAddReceipt, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        item = svc.add_receipt(
            db, user, claim_id, body.receipt_id, body.voucher_type, body.expense_type, body.note
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return ItemOut.model_validate(item, from_attributes=True)


@router.post("/{claim_id}/vouchers")
def add_voucher(
    claim_id: int, body: ItemAddVoucher, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """无票支出人工凭证（收款凭证/内部凭证/合同/境外票据）。"""
    try:
        item = svc.add_manual_voucher(
            db, user, claim_id, voucher_type=body.voucher_type, amount=Decimal(body.amount),
            expense_type=body.expense_type, note=body.note, payee_name=body.payee_name,
            payee_id_no=body.payee_id_no, attachment_url=body.attachment_url,
        )
    except (ValueError, ArithmeticError) as e:
        raise HTTPException(422, str(e)) from None
    return ItemOut.model_validate(item, from_attributes=True)


@router.delete("/items/{item_id}")
def remove_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        svc.remove_item(db, user, item_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True}


@router.post("/{claim_id}/submit", response_model=ClaimOut)
def submit_claim(claim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        claim = svc.submit_claim(db, user, claim_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return _claim_out(db, claim)


@router.post("/{claim_id}/approve", response_model=ClaimOut)
def approve_claim(claim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        claim = svc.approve_claim(db, user, claim_id)
    except ValueError as e:
        raise HTTPException(403 if "财务" in str(e) else 422, str(e)) from None
    return _claim_out(db, claim)


@router.post("/{claim_id}/reject", response_model=ClaimOut)
def reject_claim(
    claim_id: int, body: RejectBody, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        claim = svc.reject_claim(db, user, claim_id, body.reason)
    except ValueError as e:
        raise HTTPException(403 if "财务" in str(e) else 422, str(e)) from None
    return _claim_out(db, claim)


@router.post("/{claim_id}/withdraw", response_model=ClaimOut)
def withdraw_claim(claim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        claim = svc.withdraw_claim(db, user, claim_id)
    except ValueError as e:
        raise HTTPException(403 if "本人" in str(e) else 422, str(e)) from None
    return _claim_out(db, claim)
