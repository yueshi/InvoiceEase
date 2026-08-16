"""LLM 引擎双通道测试（fake client 注入，不真调 API）。"""
from datetime import date
from decimal import Decimal

from invoicing.models.enums import ParseSource
from invoicing.parse.llm import LlmEngine

GOOD_JSON = """{"invoice_code": "026617000003", "invoice_number": "26617000000309516967",
"issue_date": "2026-07-09", "seller_name": "示例出行科技有限公司", "seller_tax_id": "91310000MA1FL0A000",
"buyer_name": "测试采购有限公司", "buyer_tax_id": "91310000MA1FL0B000",
"amount_without_tax": "65.48", "tax_amount": "1.96", "total_amount": "67.44",
"total_amount_cn": "陆拾柒元肆角肆分", "invoice_type": "电子发票（普通发票）"}"""


class FakeCompletions:
    def __init__(self, content: str):
        self._content = content

    def create(self, **kwargs):
        # 记录调用参数供断言
        FakeCompletions.last_kwargs = kwargs
        return _FakeResp(self._content)


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMsg(content)


class _FakeMsg:
    def __init__(self, content: str):
        self.content = content


class _FakeResp:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


def _engine(content: str) -> LlmEngine:
    class FakeClient:
        def __init__(self, **kwargs):
            # completions 为稳定实例属性（缓存测试要替换其 create 方法）
            self.chat = type("Chat", (), {"completions": FakeCompletions(content)})()

    return LlmEngine(
        base_url="http://fake", api_key="k", model_text="m1", model_vlm="m2",
        timeout=5.0, max_retries=0, enabled=True, client_factory=lambda: FakeClient(),
    )


def test_extract_from_text_maps_fields():
    engine = _engine(GOOD_JSON)
    parsed = engine.extract_from_text("发票号码：26617000000309516967 ...")
    assert parsed is not None
    assert parsed.invoice_number == "26617000000309516967"
    assert parsed.issue_date == date(2026, 7, 9)
    assert parsed.total_amount == Decimal("67.44")
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.parse_source == ParseSource.LLM_TEXT.value
    assert parsed.confidence_score == 0.9
    # 请求格式断言：system 含安全声明、user 含原文、json_object 输出
    kwargs = FakeCompletions.last_kwargs
    messages = kwargs["messages"]
    assert any("不是指令" in m["content"] for m in messages)
    assert any("发票号码" in m["content"] for m in messages)
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["model"] == "m1"


def test_extract_from_image_vlm_source():
    engine = _engine(GOOD_JSON)
    parsed = engine.extract_from_image(b"\x89PNG fake-image")
    assert parsed is not None
    assert parsed.parse_source == ParseSource.VLM.value
    assert parsed.confidence_score == 0.85
    assert FakeCompletions.last_kwargs["model"] == "m2"


def test_missing_key_fields_returns_none():
    bad = '{"invoice_number": "26617000000309516967", "issue_date": "2026-07-09", "amount_without_tax": "", "tax_amount": "1.96", "total_amount": "67.44"}'
    engine = _engine(bad)
    assert engine.extract_from_text("x") is None


def test_client_error_returns_none():
    def boom(**kwargs):
        raise RuntimeError("api down")

    class BoomClient:
        class chat:
            @property
            def completions(self):
                return type("C", (), {"create": staticmethod(boom)})()

    engine = LlmEngine(
        base_url="http://fake", api_key="k", model_text="m1", model_vlm="m2",
        timeout=5.0, max_retries=0, enabled=True, client_factory=lambda: BoomClient(),
    )
    assert engine.extract_from_text("x") is None


def test_invalid_json_returns_none():
    engine = _engine("not json at all")
    assert engine.extract_from_text("x") is None


def test_result_cached_by_content():
    calls = {"n": 0}

    def counted_create(**kwargs):
        calls["n"] += 1
        FakeCompletions.last_kwargs = kwargs
        return _FakeResp(GOOD_JSON)

    engine = _engine(GOOD_JSON)
    engine._client.chat.completions.create = counted_create  # 换计数包装
    assert engine.extract_from_text("same text") is not None
    assert engine.extract_from_text("same text") is not None
    assert calls["n"] == 1  # sha1 缓存命中


def test_disabled_engine_returns_none():
    engine = _engine(GOOD_JSON)
    engine.enabled = False
    assert engine.extract_from_text("x") is None


def test_extract_from_image_jpeg_mime_sniffed():
    engine = _engine(GOOD_JSON)
    assert engine.extract_from_image(b"\xff\xd8\xff\xe0 fake jpeg") is not None
    url = FakeCompletions.last_kwargs["messages"][1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_non_finite_decimal_rejected():
    bad = GOOD_JSON.replace('"amount_without_tax": "65.48"', '"amount_without_tax": "NaN"')
    engine = _engine(bad)
    assert engine.extract_from_text("x") is None
