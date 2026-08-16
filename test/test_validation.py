import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path

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


def test_amount_to_cn_negative_raises():
    with pytest.raises(ValueError):
        amount_to_cn(Decimal("-1.00"))


def test_cn_yuan_variant_accepted():
    """真实发票常见「圆」写法应与「元」等价（圆/元归一）。"""
    from datetime import date
    from decimal import Decimal

    from invoicing.parse.schemas import ParsedInvoice
    from invoicing.parse.validation import validate

    parsed = ParsedInvoice(
        invoice_number="N1",
        issue_date=date(2026, 8, 1),
        amount_without_tax=Decimal("65.48"),
        tax_amount=Decimal("1.96"),
        total_amount=Decimal("67.44"),
        total_amount_cn="陆拾柒圆肆角肆分",
        seller_name="S",
        seller_tax_id="T1",
        buyer_name="B",
        buyer_tax_id="T2",
        confidence_score=0.85,
        parse_source="PDF_TEXT",
    )
    assert validate(parsed) == []


def test_validate_cn_extra_zheng_tolerated():
    """容错：尾部「整」字有无视为等价（如「叁佰陆拾肆元柒角整」vs 标准「叁佰陆拾肆元柒角」）。"""
    from invoicing.parse.schemas import ParsedInvoice
    from invoicing.parse.validation import validate

    parsed = ParsedInvoice(
        invoice_number="N1",
        issue_date=date(2026, 7, 9),
        amount_without_tax=Decimal("364.70"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("364.70"),
        total_amount_cn="叁佰陆拾肆元柒角整",  # 多「整」
        seller_name="s", seller_tax_id="t",
        buyer_name="b", buyer_tax_id="t2",
        confidence_score=0.9, parse_source="LLM_TEXT",
    )
    errors = validate(parsed)
    assert not any(e.code == "CN_MISMATCH" for e in errors)


def test_validate_cn_genuine_mismatch_still_caught():
    """真错误仍拦截：数字金额不同的写法不得因容错而通过。"""
    from invoicing.parse.schemas import ParsedInvoice
    from invoicing.parse.validation import validate

    parsed = ParsedInvoice(
        invoice_number="N1",
        issue_date=date(2026, 7, 9),
        amount_without_tax=Decimal("364.70"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("364.70"),
        total_amount_cn="叁佰陆拾元柒角",
        seller_name="s", seller_tax_id="t",
        buyer_name="b", buyer_tax_id="t2",
        confidence_score=0.9, parse_source="LLM_TEXT",
    )
    assert any(e.code == "CN_MISMATCH" for e in validate(parsed))
