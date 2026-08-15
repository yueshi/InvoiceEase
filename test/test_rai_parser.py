"""铁路电子客票 rai XBRL 解析测试（合成脱敏 fixture）。"""
import io
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree
from pypdf import PdfWriter

from invoicing.parse.rai_parser import is_rai_xbrl, parse_rai_xbrl
from invoicing.parse.validation import validate
from invoicing.parse.xbrl import extract_xml_from_pdf
from invoicing.parse.xml_parser import parse_invoice_xml

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_parse_rai_fields():
    data = (FIXTURES / "railway_rai.xml").read_bytes()
    parsed = parse_invoice_xml(data)  # 经分流走 rai 分支
    assert parsed.invoice_number == "26419122537000504214"
    assert parsed.issue_date.isoformat() == "2026-07-11"
    assert parsed.amount_without_tax == Decimal("250.00")
    assert parsed.tax_amount == Decimal("22.50")
    assert parsed.total_amount == Decimal("272.50")
    assert parsed.total_amount_cn == ""
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.invoice_type == "电子发票（铁路电子客票）"
    assert parsed.confidence_score == 1.0


def test_rai_amounts_consistent():
    parsed = parse_invoice_xml((FIXTURES / "railway_rai.xml").read_bytes())
    assert validate(parsed) == []  # 250.00+22.50=272.50；无大写字段跳过 CN 校验


def test_is_rai_xbrl_detection():
    rai_root = etree.fromstring((FIXTURES / "railway_rai.xml").read_bytes())
    ei_root = etree.fromstring((FIXTURES / "dianzi.xml").read_bytes())
    assert is_rai_xbrl(rai_root) is True
    assert is_rai_xbrl(ei_root) is False


def test_rai_missing_key_fields_raises():
    bad = (
        b'<xbrl xmlns="http://www.xbrl.org/2003/instance" '
        b'xmlns:rai="http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai">'
        b"<rai:TypeOfVoucher>x</rai:TypeOfVoucher></xbrl>"
    )
    with pytest.raises(ValueError, match="RAI_PARSE_ERROR"):
        parse_invoice_xml(bad)


def test_rai_inside_pdf_attachment_extracted():
    """12306 场景：PDF 内嵌 rai XML 附件 → 既有提取路径应能找到。"""
    rai = (FIXTURES / "railway_rai.xml").read_bytes()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_attachment("rai_issuer_test.xml", rai)
    buf = io.BytesIO()
    writer.write(buf)
    extracted = extract_xml_from_pdf(buf.getvalue())
    assert extracted is not None
    parsed = parse_invoice_xml(extracted)
    assert parsed.invoice_number == "26419122537000504214"


def test_rai_parse_direct():
    root = etree.fromstring((FIXTURES / "railway_rai.xml").read_bytes())
    parsed = parse_rai_xbrl(root)
    assert parsed.total_amount == Decimal("272.50")
