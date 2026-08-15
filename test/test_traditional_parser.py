"""传统增值税电子普通发票 XML（EInvoice/EInvoiceData schema）解析测试。"""
import io
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfWriter

from invoicing.parse.traditional_parser import is_traditional_einvoice, parse_traditional_einvoice
from invoicing.parse.validation import validate
from invoicing.parse.xbrl import extract_xml_from_pdf
from invoicing.parse.xml_parser import parse_invoice_xml
from lxml import etree

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_parse_traditional_fields():
    parsed = parse_invoice_xml((FIXTURES / "traditional_einvoice.xml").read_bytes())
    assert parsed.invoice_number == "26617000000309516967"
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert parsed.amount_without_tax == Decimal("65.48")
    assert parsed.tax_amount == Decimal("1.96")
    assert parsed.total_amount == Decimal("67.44")
    assert parsed.total_amount_cn == "陆拾柒元肆角肆分"
    assert parsed.seller_name == "示例出行科技有限公司"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.invoice_type == "增值税电子普通发票"
    assert parsed.confidence_score == 1.0


def test_traditional_amounts_consistent():
    parsed = parse_invoice_xml((FIXTURES / "traditional_einvoice.xml").read_bytes())
    assert validate(parsed) == []  # 65.48+1.96=67.44 且大写一致


def test_variant_detection():
    trad = etree.fromstring((FIXTURES / "traditional_einvoice.xml").read_bytes())
    ei = etree.fromstring((FIXTURES / "dianzi.xml").read_bytes())
    assert is_traditional_einvoice(trad) is True
    assert is_traditional_einvoice(ei) is False


def test_missing_key_fields_raises():
    bad = b"<EInvoice><Header><EIid>x</EIid></Header><EInvoiceData/></EInvoice>"
    with pytest.raises(ValueError, match="TRADITIONAL_PARSE_ERROR"):
        parse_invoice_xml(bad)


def test_traditional_inside_pdf_attachment():
    """发票 XML 作为 PDF 附件场景（与 rai 同路径）。"""
    data = (FIXTURES / "traditional_einvoice.xml").read_bytes()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_attachment("invoice.xml", data)
    buf = io.BytesIO()
    writer.write(buf)
    extracted = extract_xml_from_pdf(buf.getvalue())
    assert extracted is not None
    parsed = parse_invoice_xml(extracted)
    assert parsed.invoice_number == "26617000000309516967"


def test_parse_direct():
    root = etree.fromstring((FIXTURES / "traditional_einvoice.xml").read_bytes())
    parsed = parse_traditional_einvoice(root)
    assert parsed.total_amount == Decimal("67.44")
