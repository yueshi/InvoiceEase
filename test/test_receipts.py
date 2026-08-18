"""银行回单测试（R1：解析 + 配对建议 + 凭证草稿）。"""
from datetime import date
from decimal import Decimal

from invoicing.models import BankReceipt, Invoice
from invoicing.parse.receipt import parse_receipt_text, suggest_pair
from invoicing.reports import receipts_to_csv


def test_parse_receipt_text():
    """规则通道：金额/对方户名/日期/摘要从回单文本提取。"""
    text = (
        "交易日期 2026-08-05\n"
        "对方户名 北京某某科技有限公司\n"
        "交易金额 1,000.00\n"
        "摘要 货款\n"
    )
    parsed = parse_receipt_text(text)
    assert parsed["amount"] == Decimal("1000.00")
    assert "北京某某科技有限公司" in parsed["counterparty_name"]
    assert parsed["trade_date"] == date(2026, 8, 5)
    assert parsed["abstract"] == "货款"


def test_parse_receipt_missing_fields_returns_none():
    """关键字段缺失 → None（不产半成品，LLM 兜底由调用方处理）。"""
    assert parse_receipt_text("没有金额和户名的文本") is None


def test_pair_by_amount_and_normalized_name(db):
    """配对：金额相等 + 户名规范化后包含。"""
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345678",
        status="pending_submit", total_amount=Decimal("1000.00"),
        seller_name="北京某某科技有限公司", issue_date=date(2026, 8, 5),
    )
    db.add(inv)
    db.flush()
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="北京某某科技", amount=Decimal("1000.00"),
    )
    db.add(r)
    db.flush()
    assert suggest_pair(db, r.id) == inv.id


def test_pair_amount_mismatch_no_pair(db):
    """金额不等 → 不配对。"""
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345679",
        status="pending_submit", total_amount=Decimal("1000.00"),
        seller_name="北京某某科技有限公司", issue_date=date(2026, 8, 5),
    )
    db.add(inv)
    db.flush()
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="北京某某科技", amount=Decimal("999.99"),
    )
    db.add(r)
    db.flush()
    assert suggest_pair(db, r.id) is None


def test_receipts_csv_has_voucher_columns(db):
    """凭证草稿 CSV：金蝶/用友通用列（日期/摘要/对方户名/金额/借贷方）。"""
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="某某公司", amount=Decimal("1000.00"), abstract="货款",
    )
    db.add(r)
    db.flush()
    csv_data = receipts_to_csv(db, "2026-08").decode("utf-8-sig")
    header = csv_data.splitlines()[0]
    assert "日期" in header
    assert "借方" in header
    assert "1000.00" in csv_data
