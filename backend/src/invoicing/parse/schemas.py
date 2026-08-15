from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ParsedInvoice(BaseModel):
    invoice_code: str | None = None
    invoice_number: str
    issue_date: date
    amount_without_tax: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    total_amount_cn: str
    seller_name: str
    seller_tax_id: str
    buyer_name: str
    buyer_tax_id: str
    invoice_type: str | None = None
    confidence_score: float
    parse_source: str


class ParseError(BaseModel):
    code: str
    message: str


class ParseOutcome(BaseModel):
    source: str | None
    parsed: ParsedInvoice | None
    errors: list[ParseError]
    xml_data: bytes | None = None  # 提取出的 XML 原文（合规归档用）
