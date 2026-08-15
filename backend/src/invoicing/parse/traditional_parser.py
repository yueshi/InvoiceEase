"""传统增值税电子普通发票 XML 解析（EInvoice/EInvoiceData schema，真机实测）。

与数电票（root eInvoice + EInvoiceHeader）是两套 schema：
- 号码 = Header/EIid（20 位）
- 开票日期 = EInvoiceData/BasicInformation/RequestTime（datetime，取日期部分）
- 金额 = TotalAmWithoutTax / TotalTaxAm / TotalTax-includedAmount / 大写 TotalTax-includedAmountInChinese
- 购销方 = SellerInformation / BuyerInformation（IdNum + Name）
"""
from datetime import datetime
from decimal import Decimal

from lxml import etree

from invoicing.parse.schemas import ParsedInvoice

TRADITIONAL_TYPE = "增值税电子普通发票"


def is_traditional_einvoice(root: etree._Element) -> bool:
    return any(el.tag.rsplit("}", 1)[-1] == "EInvoiceData" for el in root.iter())


def _first_text(root: etree._Element, name: str) -> str | None:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == name and el.text is not None and el.text.strip():
            return el.text.strip()
    return None


def parse_traditional_einvoice(root: etree._Element) -> ParsedInvoice:
    number = _first_text(root, "EIid")
    request_time = _first_text(root, "RequestTime")
    if not number or not request_time:
        raise ValueError("TRADITIONAL_PARSE_ERROR: 缺少 EIid/RequestTime")
    try:
        issue_date = datetime.fromisoformat(request_time).date()
    except ValueError as e:
        raise ValueError(f"TRADITIONAL_PARSE_ERROR: 开票时间格式非法 {request_time}") from e
    return ParsedInvoice(
        invoice_number=number,
        issue_date=issue_date,
        amount_without_tax=Decimal(_first_text(root, "TotalAmWithoutTax") or "0"),
        tax_amount=Decimal(_first_text(root, "TotalTaxAm") or "0"),
        total_amount=Decimal(_first_text(root, "TotalTax-includedAmount") or "0"),
        total_amount_cn=_first_text(root, "TotalTax-includedAmountInChinese") or "",
        seller_name=_first_text(root, "SellerName") or "",
        seller_tax_id=_first_text(root, "SellerIdNum") or "",
        buyer_name=_first_text(root, "BuyerName") or "",
        buyer_tax_id=_first_text(root, "BuyerIdNum") or "",
        invoice_type=TRADITIONAL_TYPE,
        confidence_score=1.0,
        parse_source="XML",
    )
