"""纠错字典与归属校验测试。"""
from datetime import date
from decimal import Decimal

from invoicing.models import CompanyInfo
from invoicing.parse.company_dict import enrich_parsed
from invoicing.parse.schemas import ParsedInvoice


def _parsed(
    buyer_name="澜鸿欣（上海）数字科技有限公司",
    buyer_tax_id="91310101MAELA36R35",
    seller_name="山东及时雨汽车科技有限公司西安分公司",
    confidence_score=0.95,
) -> ParsedInvoice:
    return ParsedInvoice(
        invoice_number="N1",
        issue_date=date(2026, 7, 9),
        amount_without_tax=Decimal("65.48"),
        tax_amount=Decimal("1.96"),
        total_amount=Decimal("67.44"),
        total_amount_cn="",
        seller_name=seller_name,
        seller_tax_id="91610132MA6UY02A5U",
        buyer_name=buyer_name,
        buyer_tax_id=buyer_tax_id,
        confidence_score=confidence_score,
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


def test_no_self_configured_skips_nonempty_dict(db):
    """非空库但无 kind=self（仅有供应商/其他）→ 跳过归属校验。"""
    db.add(CompanyInfo(name="某供应商", tax_id="91610132MA6UY02A5U", kind="supplier"))
    db.add(CompanyInfo(name="某其他", tax_id="91320594MA1MFD7F31", kind="other"))
    db.flush()
    parsed = _parsed(buyer_tax_id="91310000MA1FL0B000")  # 不匹配任何预设
    assert enrich_parsed(parsed, db) == []


def test_seller_fuzzy_correction(db):
    """seller 侧同样走模糊纠错。"""
    db.add(CompanyInfo(name="山东及时雨汽车科技有限公司西安分公司", tax_id="99999999999999999X", kind="supplier"))
    db.flush()
    # seller_tax_id 默认 91610132MA6UY02A5U 不匹配预设税号 → 走模糊匹配
    parsed = _parsed(seller_name="山东及时雨汽车科技有限公司安分公司")  # 漏「西」字
    errors = enrich_parsed(parsed, db)
    assert parsed.seller_name == "山东及时雨汽车科技有限公司西安分公司"
    assert errors == []  # 无 kind=self → 无 BUYER_MISMATCH


def test_structured_confidence_1_0_no_correction(db):
    """结构化来源（confidence=1.0）不纠错：原件数据优先。"""
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed(confidence_score=1.0)
    assert enrich_parsed(parsed, db) == []
    assert parsed.buyer_name == "澜鸿欣（上海）数字科技有限公司"  # 未纠错


def test_none_confidence_no_correction(db):
    """防御性：confidence 缺失时同样不纠错不校验。"""
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.flush()
    parsed = _parsed()
    parsed.confidence_score = None  # 直接赋值模拟缺失置信度（schema 未禁止运行时赋值）
    assert enrich_parsed(parsed, db) == []
    assert parsed.buyer_name == "澜鸿欣（上海）数字科技有限公司"
