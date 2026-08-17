"""复核预判测试（规则层确定性断言；LLM 层 fake 注入）。"""
from datetime import date
from decimal import Decimal

from invoicing.models import Invoice
from invoicing.parse.ai_review import ReviewVerdict, predict_review

HARD_CODES = [{"code": "TOTAL_MISMATCH", "message": "金额矛盾"}]


class _FakeEngine:
    """fake LlmEngine：按预设回复，验证 ai_review 的 LLM 路径。"""

    def __init__(self, reply: str | None = None, exc: Exception | None = None):
        self._reply = reply
        self._exc = exc

    def chat_json(self, system_prompt: str, user_content: str) -> str | None:
        if self._exc is not None:
            raise self._exc
        return self._reply


def _invoice(db, errors=None, **kw) -> Invoice:
    inv = Invoice(
        file_url="a.xml",
        file_type="XML",
        invoice_number=kw.get("invoice_number", "24312000000012345678"),
        status="pending_review",
        total_amount=kw.get("total_amount", Decimal("1000.00")),
        amount_without_tax=Decimal("943.40"),
        tax_amount=Decimal("56.60"),
        seller_name=kw.get("seller_name", "示例科技有限公司"),
        seller_tax_id="91310000MA1FL0A000",
        buyer_name=kw.get("buyer_name", "测试采购有限公司"),
        buyer_tax_id="91310000MA1FL0B000",
        issue_date=date(2026, 8, 1),
        parse_source=kw.get("parse_source", "XML"),
        confidence_score=kw.get("confidence_score", 1.0),
        validation_errors=errors,
    )
    db.add(inv)
    db.flush()
    return inv


def test_rule_reject_on_hard_codes(db, monkeypatch):
    """规则拦截：金额矛盾直接 reject，conf=1.0，不调 LLM。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: None)
    inv = _invoice(db, errors=HARD_CODES)
    v = predict_review(inv, db)
    assert v is not None
    assert v.verdict == "reject"
    assert "TOTAL_MISMATCH" in v.reason
    assert v.confidence == 1.0


def test_rule_approve_when_clean_and_complete(db, monkeypatch):
    """规则通过：无错误且关键字段齐全 → approve，conf=1.0。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: None)
    inv = _invoice(db, errors=[])
    v = predict_review(inv, db)
    assert v is not None
    assert v.verdict == "approve"
    assert v.confidence == 1.0


def test_low_ocr_confidence_gets_uncertain(db, monkeypatch):
    """OCR 弱票（<0.8）即使字段齐全无错误也不得规则 approve——FRD 人工复核底线。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: None)  # 不调 LLM，门槛分支直接给 uncertain
    inv = _invoice(db, errors=[], parse_source="PDF_OCR", confidence_score=0.72)
    v = predict_review(inv, db)
    assert v is not None
    assert v.verdict == "uncertain"
    assert "0.72" in v.reason


def test_llm_fallback_returns_none_when_unavailable(db, monkeypatch):
    """边缘场景且 LLM 不可用 → None（不生成预判，人工照旧）。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: None)
    inv = _invoice(db, errors=[], buyer_name="")  # 字段缺失 → 边缘
    assert predict_review(inv, db) is None


def test_llm_verdict_accepted_with_reason(db, monkeypatch):
    """LLM 正常返回：verdict/reason/confidence 解析并 clamp。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(
        mod, "get_llm_engine",
        lambda: _FakeEngine('{"verdict": "uncertain", "reason": "仅缺购买方名称，OCR 弱票", "confidence": 0.6}'),
    )
    inv = _invoice(db, errors=[], buyer_name="")
    v = predict_review(inv, db)
    assert v is not None
    assert v.verdict == "uncertain"
    assert "购买方" in v.reason
    assert v.confidence == 0.6


def test_llm_garbage_returns_none(db, monkeypatch):
    """LLM 畸形输出（非 JSON/非法 verdict）→ None，不落错误预判。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: _FakeEngine("{"))
    inv = _invoice(db, errors=[], buyer_name="")
    assert predict_review(inv, db) is None
    monkeypatch.setattr(
        mod, "get_llm_engine",
        lambda: _FakeEngine('{"verdict": "maybe", "reason": "x", "confidence": 0.5}'),
    )
    assert predict_review(inv, db) is None


def test_llm_exception_returns_none(db, monkeypatch):
    """LLM 抛异常 → None（降级安全）。"""
    import invoicing.parse.ai_review as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: _FakeEngine(exc=RuntimeError("boom")))
    inv = _invoice(db, errors=[], buyer_name="")
    assert predict_review(inv, db) is None
