from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoicing.parse.xml_parser import parse_invoice_xml

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_parse_dianzi_xml():
    data = (FIXTURES / "dianzi.xml").read_bytes()
    parsed = parse_invoice_xml(data)
    assert parsed.invoice_number == "24312000000012345678"
    assert parsed.invoice_code is None
    assert parsed.issue_date == date(2026, 8, 1)
    assert parsed.amount_without_tax == Decimal("909.09")
    assert parsed.tax_amount == Decimal("90.91")
    assert parsed.total_amount == Decimal("1000.00")
    assert parsed.total_amount_cn == "壹仟元整"
    assert parsed.seller_name == "示例科技有限公司"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.invoice_type == "81"
    assert parsed.confidence_score == 1.0
    assert parsed.parse_source == "XML"


def test_parse_invalid_xml_raises():
    with pytest.raises(ValueError, match="XML_PARSE_ERROR"):
        parse_invoice_xml(b"<not-an-invoice/>")
