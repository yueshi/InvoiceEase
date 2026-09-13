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


class EntryCreate(BaseModel):
    entry_type: str = "other"
    title: str = Field(min_length=1, max_length=256)
    occurred_on: str | None = None  # YYYY-MM-DD
    scene_fields: dict | None = None
    note: str | None = Field(default=None, max_length=512)


class EntryUpdate(BaseModel):
    entry_type: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=256)
    occurred_on: str | None = None
    scene_fields: dict | None = None
    note: str | None = Field(default=None, max_length=512)


class EntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    claim_id: int
    entry_type: str
    title: str
    occurred_on: str | None
    scene_fields: dict | None
    amount: MoneyStr
    note: str | None


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
    entry_id: int | None
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


def _entry_out(e) -> EntryOut:
    return EntryOut(
        id=e.id, claim_id=e.claim_id, entry_type=e.entry_type, title=e.title,
        occurred_on=str(e.occurred_on) if e.occurred_on else None,
        scene_fields=e.scene_fields, amount=e.amount, note=e.note,
    )


@router.get("/{claim_id}")
def claim_detail(claim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单据详情：事项分组（每项含其凭证）+ 平铺凭证列表。"""
    try:
        claim, entries, items = svc.claim_detail(db, user, claim_id)
    except ValueError as e:
        raise HTTPException(404 if "不存在" in str(e) else 403, str(e)) from None
    items_out = [ItemOut.model_validate(i, from_attributes=True) for i in items]
    entry_rows = []
    for e in entries:
        row = _entry_out(e).model_dump(mode="json")  # MoneyStr 仅在 json 模式生效
        row["items"] = [
            it.model_dump(mode="json") for it in items_out if it.entry_id == e.id
        ]
        entry_rows.append(row)
    return {
        "claim": _claim_out(db, claim).model_dump(mode="json"),
        "entries": entry_rows,
        "items": [it.model_dump(mode="json") for it in items_out],
    }


@router.post("/{claim_id}/entries", response_model=EntryOut)
def create_entry(
    claim_id: int, body: EntryCreate, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新建事项（费用明细行）；场景必填要素缺失 → 422。"""
    from datetime import date as _date

    occurred = None
    if body.occurred_on:
        try:
            occurred = _date.fromisoformat(body.occurred_on)
        except ValueError:
            raise HTTPException(422, "occurred_on 需为 YYYY-MM-DD") from None
    try:
        entry = svc.create_entry(
            db, user, claim_id, body.entry_type, body.title, occurred,
            body.scene_fields, body.note,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return _entry_out(entry)


@router.patch("/{claim_id}/entries/{entry_id}", response_model=EntryOut)
def update_entry(
    claim_id: int, entry_id: int, body: EntryUpdate, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from datetime import date as _date

    fields = body.model_dump(exclude_unset=True)
    if fields.get("occurred_on"):
        try:
            fields["occurred_on"] = _date.fromisoformat(fields["occurred_on"])
        except ValueError:
            raise HTTPException(422, "occurred_on 需为 YYYY-MM-DD") from None
    try:
        entry = svc.update_entry(db, user, entry_id, **fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return _entry_out(entry)


@router.delete("/{claim_id}/entries/{entry_id}")
def remove_entry(
    claim_id: int, entry_id: int, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除事项（连同其凭证；占用的发票释放）。"""
    try:
        svc.remove_entry(db, user, entry_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True}


@router.post("/{claim_id}/entries/{entry_id}/invoices")
def add_invoice_to_entry(
    claim_id: int, entry_id: int, body: ItemAddInvoice, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        item = svc.add_invoice(db, user, claim_id, body.invoice_id, entry_id,
                               body.expense_type, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return ItemOut.model_validate(item, from_attributes=True)


@router.post("/{claim_id}/entries/{entry_id}/receipts")
def add_receipt_to_entry(
    claim_id: int, entry_id: int, body: ItemAddReceipt, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        item = svc.add_receipt(db, user, claim_id, body.receipt_id, entry_id,
                               body.voucher_type, body.expense_type, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return ItemOut.model_validate(item, from_attributes=True)


@router.post("/{claim_id}/entries/{entry_id}/vouchers")
def add_voucher_to_entry(
    claim_id: int, entry_id: int, body: ItemAddVoucher, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """无票支出人工凭证（收款凭证/内部凭证/合同/境外票据）。"""
    try:
        item = svc.add_manual_voucher(
            db, user, claim_id, entry_id, voucher_type=body.voucher_type,
            amount=Decimal(body.amount), expense_type=body.expense_type, note=body.note,
            payee_name=body.payee_name, payee_id_no=body.payee_id_no,
            attachment_url=body.attachment_url,
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


@router.delete("/{claim_id}")
def delete_claim(claim_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """删除报销单（审计快照 + 释放发票占用）。"""
    try:
        return svc.delete_claim(db, user, claim_id)
    except ValueError as e:
        raise HTTPException(403 if "无权" in str(e) else 404, str(e)) from None


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
