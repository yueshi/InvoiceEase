"""质量门与 LLM 三触发点测试（FakeLlmEngine/FakeProvider 注入，不真调 API/OCR）。"""
from datetime import date
from decimal import Decimal

from invoicing.models.enums import ParseSource
from invoicing.parse import ocr as ocr_mod
from invoicing.parse import pdf_text_parser as ptp_mod
from invoicing.parse import pipeline as pipeline_mod
from invoicing.parse.ocr import OcrText
from invoicing.parse.pipeline import ParseContext, parse_file, strategy_llm_vlm, strategy_ocr_pdf, strategy_text_pdf, _needs_llm
from invoicing.parse.schemas import ParsedInvoice

OCR_TEXT = (
    "电子发票（普通发票） 发票号码：26617000000309516967\n"
    "开票日期：2026年07月09日\n"
    "名称：测试采购有限公司\n"
    "统一社会信用代码/纳税人识别号：91310000MA1FL0B000\n"
    "名称：示例出行科技有限公司\n"
    "统一社会信用代码/纳税人识别号：91310000MA1FL0A000\n"
    "合    计 ¥65.48 ¥1.96\n"
)


def _llm_parsed() -> ParsedInvoice:
    return ParsedInvoice(
        invoice_number="26617000000309516967",
        issue_date=date(2026, 7, 9),
        amount_without_tax=Decimal("65.48"),
        tax_amount=Decimal("1.96"),
        total_amount=Decimal("67.44"),
        total_amount_cn="",
        seller_name="示例出行科技有限公司",
        seller_tax_id="91310000MA1FL0A000",
        buyer_name="测试采购有限公司",
        buyer_tax_id="91310000MA1FL0B000",
        confidence_score=0.9,
        parse_source="LLM_TEXT",
    )


class FakeLlmEngine:
    def __init__(self, text_result=None, image_result=None):
        self.text_result = text_result
        self.image_result = image_result
        self.text_calls: list[str] = []
        self.image_calls: list[bytes] = []

    def extract_from_text(self, text: str):
        self.text_calls.append(text)
        return self.text_result

    def extract_from_image(self, image: bytes):
        self.image_calls.append(image)
        return self.image_result


class FakeProvider:
    def __init__(self, text: str, confidence: float):
        self._text = text
        self._confidence = confidence

    def ocr_image(self, image_bytes: bytes) -> OcrText:
        return OcrText(text=self._text, confidence=self._confidence)


def _patch(monkeypatch, provider, engine):
    monkeypatch.setattr(ocr_mod, "get_ocr_provider", lambda: provider)
    monkeypatch.setattr(pipeline_mod, "get_llm_engine", lambda: engine)
    monkeypatch.setattr(ocr_mod, "render_pdf_first_page", lambda b: b"png-bytes")


def test_quality_gate_text_rules_fail_triggers_llm(monkeypatch):
    """文本层无发票内容 → 文本规则失败 → LLM 文本通道成功 → LLM_TEXT 来源。"""
    engine = FakeLlmEngine(text_result=_llm_parsed())
    _patch(monkeypatch, FakeProvider(OCR_TEXT, 0.92), engine)
    monkeypatch.setattr(ptp_mod, "extract_pdf_text", lambda data: "无发票关键字段的文本")
    outcome = strategy_text_pdf(ParseContext("PDF", b"%PDF fake"))
    assert outcome is not None
    assert outcome.source == ParseSource.LLM_TEXT.value
    assert engine.text_calls  # LLM 文本通道被调用
# --- 质量门单测 ---

def test_gate_parsed_none():
    assert _needs_llm(None, None) is True


def test_gate_missing_two_fields():
    parsed = _llm_parsed().model_copy(update={"buyer_name": "", "seller_name": ""})
    assert _needs_llm(parsed, None) is True


def test_gate_one_missing_ok():
    parsed = _llm_parsed().model_copy(update={"buyer_name": ""})
    assert _needs_llm(parsed, None) is False


def test_gate_low_ocr_confidence():
    assert _needs_llm(_llm_parsed(), 0.72) is True
    assert _needs_llm(_llm_parsed(), 0.92) is False


# --- 三触发点：OCR 低置信度 → LLM 文本通道 ---

def test_ocr_low_confidence_triggers_llm(monkeypatch):
    engine = FakeLlmEngine(text_result=_llm_parsed())
    _patch(monkeypatch, FakeProvider(OCR_TEXT, confidence=0.72), engine)
    outcome = strategy_ocr_pdf(ParseContext("PDF", b"%PDF fake"))
    assert outcome is not None
    assert outcome.source == ParseSource.LLM_TEXT.value
    assert outcome.parsed.confidence_score == 0.9
    assert engine.text_calls  # LLM 文本通道被调用


# --- LLM 失败 → 返回 None 继续链（VLM 兜底）---

def test_llm_fail_continues_to_vlm(monkeypatch):
    engine = FakeLlmEngine(text_result=None, image_result=_llm_parsed())
    _patch(monkeypatch, FakeProvider(OCR_TEXT, 0.72), engine)
    outcome = parse_file("PDF", b"%PDF no text")
    # 文本层无内容（extract_pdf_text 返回 None）→ OCR 低置信度 → LLM 文本失败 → llm_vlm 成功
    assert outcome.source == ParseSource.VLM.value
    assert engine.text_calls and engine.image_calls


def test_llm_disabled_chain_returns_unstructured(monkeypatch):
    """llm_enabled=False（get_llm_engine 返回 None）→ 链末 PDF_UNSTRUCTURED，等价现状。

    注意：OCR 文本必须不可解析——若可解析且置信度 0.92 ≥ 0.8，质量门放行，
    链在 OCR 策略即终止（PDF_OCR，与现状一致），走不到链末。
    """
    _patch(monkeypatch, FakeProvider("无发票关键字段的文本", 0.92), None)
    outcome = parse_file("PDF", b"%PDF no text")
    assert outcome.source == ParseSource.PDF_UNSTRUCTURED.value
    assert outcome.parsed is None


def test_vlm_strategy_uses_cached_image(monkeypatch):
    engine = FakeLlmEngine(image_result=_llm_parsed())
    _patch(monkeypatch, FakeProvider(OCR_TEXT, 0.92), engine)
    ctx = ParseContext("IMAGE", b"\xff\xd8 fake-image")
    outcome = strategy_llm_vlm(ctx)
    assert outcome is not None
    assert outcome.source == ParseSource.VLM.value
    assert engine.image_calls == [b"\xff\xd8 fake-image"]  # IMAGE 直接看图
