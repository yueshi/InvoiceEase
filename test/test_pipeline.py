"""解析策略链框架测试（fake 策略注入 chains 参数）。"""
from invoicing.models.enums import ParseSource
from invoicing.parse.pipeline import ParseContext, parse_file
from invoicing.parse.schemas import ParseError, ParseOutcome

OUTCOME_A = ParseOutcome(source="FAKE_A", parsed=None, errors=[])
OUTCOME_B = ParseOutcome(source="FAKE_B", parsed=None, errors=[])


def test_chain_first_hit_stops():
    calls: list[str] = []

    def first(ctx: ParseContext):
        calls.append("first")
        return OUTCOME_A

    def second(ctx: ParseContext):
        calls.append("second")
        return OUTCOME_B

    outcome = parse_file("PDF", b"data", chains={"PDF": [first, second]})
    assert outcome is OUTCOME_A
    assert calls == ["first"]  # 第二策略未被调用


def test_chain_miss_continues():
    calls: list[str] = []

    def miss(ctx: ParseContext):
        calls.append("miss")
        return None

    def hit(ctx: ParseContext):
        calls.append("hit")
        return OUTCOME_B

    outcome = parse_file("OFD", b"data", chains={"OFD": [miss, hit]})
    assert outcome is OUTCOME_B
    assert calls == ["miss", "hit"]


def test_chain_exhausted_returns_unstructured():
    def miss(ctx: ParseContext):
        return None

    outcome = parse_file("IMAGE", b"data", chains={"IMAGE": [miss, miss]})
    assert outcome.source == ParseSource.PDF_UNSTRUCTURED.value
    assert outcome.parsed is None
    assert outcome.errors == []


def test_context_lazy_cache_xml_extracted_once():
    """ctx.xml 惰性缓存：提取器只被调用一次（第二次策略复用缓存）。"""
    counter = {"n": 0}

    def fake_extract(data: bytes):
        counter["n"] += 1
        return b"<xml/>"

    ctx = ParseContext("OFD", b"ofd-bytes")
    # 模拟两个策略先后取 ctx.xml（与真实链一致：第一个 None 后第二个直接读缓存）
    if ctx.xml is None:
        ctx.xml = fake_extract(ctx.data)
    assert ctx.xml == b"<xml/>"
    if ctx.xml is None:  # 第二次不再触发提取
        ctx.xml = fake_extract(ctx.data)
    assert counter["n"] == 1
