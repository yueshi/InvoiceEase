from datetime import date
from decimal import Decimal

from lxml import etree

from invoicing.models.enums import ParseSource
from invoicing.parse.schemas import ParsedInvoice


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_text(root, *names: str) -> str | None:
    wanted = set(names)
    for el in root.iter():
        if _local_name(el.tag) in wanted and el.text is not None:
            return el.text.strip()
    return None


def parse_invoice_xml(data: bytes) -> ParsedInvoice:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"XML_PARSE_ERROR: {e}")

    # 变体分流：铁路电子客票 rai XBRL（数电票 schema 优先，其余按 xbrl+rai 判定）
    from invoicing.parse.rai_parser import is_rai_xbrl, parse_rai_xbrl

    if is_rai_xbrl(root):
        return parse_rai_xbrl(root)

    number = _first_text(root, "EInvoiceNumber", "InvoiceNumber")
    issue_date = _first_text(root, "IssueDate")
    if not number or not issue_date:
        raise ValueError(
            "XML_PARSE_ERROR: 缺少 EInvoiceNumber/IssueDate，不是数电票 XML"
        )
    try:
        parsed_date = date.fromisoformat(issue_date)
    except ValueError as e:
        raise ValueError(f"XML_PARSE_ERROR: 开票日期格式非法 {issue_date}")

    return ParsedInvoice(
        invoice_number=number,
        issue_date=parsed_date,
        amount_without_tax=Decimal(_first_text(root, "TotalAmountExcludingTax") or "0"),
        tax_amount=Decimal(_first_text(root, "TotalTaxAmount") or "0"),
        total_amount=Decimal(_first_text(root, "AmountInFigures") or "0"),
        total_amount_cn=_first_text(root, "AmountInWords") or "",
        seller_name=_first_text(root, "SellerName") or "",
        seller_tax_id=_first_text(root, "SellerTaxpayerIdentificationNumber") or "",
        buyer_name=_first_text(root, "BuyerName") or "",
        buyer_tax_id=_first_text(root, "BuyerTaxpayerIdentificationNumber") or "",
        invoice_type=_first_text(root, "EInvoiceType"),
        confidence_score=1.0,
        parse_source=ParseSource.XML.value,
    )
