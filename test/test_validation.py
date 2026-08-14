from decimal import Decimal
from pathlib import Path

from invoicing.parse.schemas import ParseError
from invoicing.parse.validation import amount_to_cn, validate
from invoicing.parse.xml_parser import parse_invoice_xml

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_amount_to_cn():
    assert amount_to_cn(Decimal("1000.00")) == "壹仟元整"
    assert amount_to_cn(Decimal("100.05")) == "壹佰元零伍分"
    assert amount_to_cn(Decimal("105.30")) == "壹佰零伍元叁角"
    assert amount_to_cn(Decimal("0")) == "零元整"
    assert amount_to_cn(Decimal("100000000.00")) == "壹亿元整"


def test_validate_clean_invoice():
    parsed = parse_invoice_xml((FIXTURES / "dianzi.xml").read_bytes())
    assert validate(parsed) == []


def test_validate_total_mismatch():
    parsed = parse_invoice_xml((FIXTURES / "dianzi_bad_total.xml").read_bytes())
    errors = validate(parsed)
    assert any(e.code == "TOTAL_MISMATCH" for e in errors)


def test_validate_cn_mismatch():
    parsed = parse_invoice_xml((FIXTURES / "dianzi_bad_cn.xml").read_bytes())
    errors = validate(parsed)
    assert any(e.code == "CN_MISMATCH" for e in errors)
