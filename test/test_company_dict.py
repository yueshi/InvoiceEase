"""纠错字典与归属校验测试。"""
from datetime import date
from decimal import Decimal

from invoicing.models import CompanyInfo
from invoicing.parse.company_dict import enrich_parsed
from invoicing.parse.schemas import ParsedInvoice


def _parsed(buyer_name="澜鸿欣（上海）数字科技有限公司", buyer_tax_id="91310101MAELA36R35") -> ParsedInvoice:
    return ParsedInvoice(
        invoice_number="N1",
        issue_date=date(2026, 7, 9),
        amount_without_tax=Decimal("65.48"),
        tax_amount=Decimal("1.96"),
        total_amount=Decimal("67.44"),
        total_amount_cn="",
        seller_name="山东及时雨汽车科技有限公司西安分公司",
        seller_tax_id="91610132MA6UY02A5U",
        buyer_name=buyer_name,
        buyer_tax_id=buyer_tax_id,
        confidence_score=0.95,
        parse_source="OFD_TEXT",
    )


def test_fuzzy_name_correction(db):
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed()
    errors = enrich_parsed(parsed, db)
    assert parsed.buyer_name == "澜铮鸿欣（上海）数字科技有限公司"  # 模糊匹配纠错
    assert errors == []  # 税号匹配本司，无 BUYER_MISMATCH


def test_tax_id_match_prefers_preset(db):
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed(buyer_name="完全不同的名字")
    errors = enrich_parsed(parsed, db)
    assert parsed.buyer_name == "澜铮鸿欣（上海）数字科技有限公司"  # 税号精确匹配优先


def test_buyer_mismatch_adds_error(db):
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed(buyer_tax_id="91320594MA1MFD7F31")  # 其他公司税号
    errors = enrich_parsed(parsed, db)
    assert any(e.code == "BUYER_MISMATCH" for e in errors)


def test_no_self_configured_skips(db):
    parsed = _parsed()
    assert enrich_parsed(parsed, db) == []  # 未配置本司 → 跳过校验


def test_empty_dict_noop(db):
    parsed = _parsed()
    assert enrich_parsed(parsed, db) == []
    assert parsed.buyer_name == "澜鸿欣（上海）数字科技有限公司"
