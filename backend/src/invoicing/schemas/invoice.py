# schemas/invoice.py
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    user_id: int | None
    mailbox_id: int | None
    email_subject: str | None
    invoice_code: str | None
    invoice_number: str | None
    issue_date: date | None
    amount_without_tax: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    total_amount_cn: str | None
    seller_name: str | None
    seller_tax_id: str | None
    buyer_name: str | None
    buyer_tax_id: str | None
    invoice_type: str | None
    file_type: str
    parse_source: str | None
    confidence_score: float | None
    validation_errors: dict | None
    verify_status: str
    verify_detail: dict | None
    verified_at: datetime | None
    duplicate_flag: bool
    duplicate_of_id: int | None
    status: str
    review_note: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InvoiceListResponse(BaseModel):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
