"""版式 PDF 文本层规则提取测试（合成脱敏文本）。"""
from decimal import Decimal
from pathlib import Path

from invoicing.parse.text_rules import extract_fields_from_text

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_standard_layout():
    text = (FIXTURES / "pdf_layout_standard.txt").read_text()
    parsed = extract_fields_from_text(text)
    assert parsed is not None
    assert parsed.invoice_number == "26617000000309516967"
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert parsed.amount_without_tax == Decimal("65.48")
    assert parsed.tax_amount == Decimal("1.96")
    assert parsed.total_amount == Decimal("67.44")
    assert parsed.total_amount_cn == "陆拾柒元肆角肆分"
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.seller_name == "示例出行科技有限公司"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.confidence_score == 0.85


def test_broken_layout():
    text = (FIXTURES / "pdf_layout_broken.txt").read_text()
    parsed = extract_fields_from_text(text)
    assert parsed is not None
    assert parsed.invoice_number == "26327000001251594557"
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert parsed.amount_without_tax == Decimal("126.89")
    assert parsed.tax_amount == Decimal("2.53")
    assert parsed.total_amount == Decimal("129.42")
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.seller_name == "示例出行科技有限公司"


def test_missing_key_fields_returns_none():
    assert extract_fields_from_text("这是一段无关文本") is None
    assert extract_fields_from_text("发票号码：123 开票日期：2026年1月1日") is None  # 缺金额


def test_amounts_consistent_after_extract():
    from invoicing.parse.validation import validate

    parsed = extract_fields_from_text((FIXTURES / "pdf_layout_standard.txt").read_text())
    assert validate(parsed) == []  # 65.48+1.96=67.44 且大写一致


def test_no_yen_total_fallback():
    """无 ¥ 符号的合计行：税率锚定 + 向左找金额（真实高德 OFD 布局）。"""
    text = (
        "发票号码：26327000001251594557\n"
        "开票日期：2026年07月09日\n"
        "合 计\n"
        "*交通运输服务*客运服务费 126.88 3% 3.81\n"
    )
    parsed = extract_fields_from_text(text)
    assert parsed is not None
    assert parsed.amount_without_tax == Decimal("126.88")
    assert parsed.tax_amount == Decimal("3.81")
    assert parsed.total_amount == Decimal("130.69")


def test_squashed_invoice_number_and_date():
    """票号与日期粘连（真实曹操 OFD 文本层）：20 位票号取前 20 位。"""
    text = (
        "合计开票人：263270000012515945572026年07月09日\n"
        "合 计\n"
        "¥126.88¥3.81\n"
    )
    parsed = extract_fields_from_text(text)
    assert parsed is not None
    assert parsed.invoice_number == "26327000001251594557"
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert parsed.total_amount == Decimal("130.69")
