"""费用归类建议测试（规则优先；LLM 兜底路径用 fake 引擎注入验证）。"""
from invoicing.parse.classify import EXPENSE_TYPES, suggest_expense_type


class _FakeEngine:
    """fake LlmEngine：按预设回复，验证 classify 的 LLM 路径（不依赖真实 LLM）。"""

    def __init__(self, reply: str):
        self._reply = reply

    def chat_json(self, system_prompt: str, user_content: str) -> str:
        return self._reply


def test_travel_keywords():
    assert suggest_expense_type("高德打车科技有限公司", None) == "travel"
    assert suggest_expense_type("中国东方航空股份有限公司", None) == "travel"
    assert suggest_expense_type("携程旅行社有限公司", None) == "travel"


def test_entertainment_and_office():
    assert suggest_expense_type("某某餐饮管理有限公司", None) == "entertainment"
    assert suggest_expense_type("某某办公用品有限公司", None) == "office"


def test_unknown_returns_other():
    assert suggest_expense_type("某某科技有限公司", None) == "other"


def test_expense_types_order():
    assert EXPENSE_TYPES == ("travel", "office", "entertainment", "procurement", "other")


def test_llm_suggests_when_rule_misses(monkeypatch):
    """规则未命中时 LLM 判断：fake 引擎返回合法类别被采纳。"""
    import invoicing.parse.classify as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: _FakeEngine("travel"))
    assert suggest_expense_type("某某科技有限公司", None) == "travel"


def test_llm_garbage_falls_back_to_other(monkeypatch):
    """LLM 返回非枚举值（花括号 JSON/未知名词）→ 兜底 other。"""
    import invoicing.parse.classify as mod

    monkeypatch.setattr(mod, "get_llm_engine", lambda: _FakeEngine("{"))
    assert suggest_expense_type("某某科技有限公司", None) == "other"
    monkeypatch.setattr(mod, "get_llm_engine", lambda: _FakeEngine("catering"))  # 非法类别
    assert suggest_expense_type("某某科技有限公司", None) == "other"


def test_llm_exception_falls_back_to_other(monkeypatch):
    """LLM 抛异常 → 兜底 other（降级安全，不阻断归类流程）。"""
    import invoicing.parse.classify as mod

    class _Boom:
        def chat_json(self, *args):
            raise RuntimeError("boom")

    monkeypatch.setattr(mod, "get_llm_engine", lambda: _Boom())
    assert suggest_expense_type("某某科技有限公司", None) == "other"
