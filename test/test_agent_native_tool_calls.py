"""模型原生工具调用方言（全角竖线包裹的标签块）的兜底解析。

背景：DeepSeek 类模型偶发把「原生工具调用标记」当纯文本输出（用户会话落库实证），
驱动层只认 prompt-JSON 协议时会把它当 final text 直通前端、工具从不执行。
本模块测试驱动层对该方言的渐进探测与整段解析。

注意：源码一律用转义/分段拼接构造标记（避免整段标签出现在源码、日志或工具输出里
被二次解析）；调试输出前把 _BAR 替换为 [DSML] 再打印。
"""
import json

from invoicing.agent.core.driver import (
    _DSML_OPEN,
    OpenAIDriver,
    _parse_assistant_reply,
    _parse_native_tool_calls,
    _probe_stream_buffer,
)

# 全角竖线包裹的分隔串（U+FF5C）；分段定义避免原始形态出现在别处
_BAR = "｜｜DSML｜｜"
# 关键字分段：避免「invoke name=...」整段出现在源码/日志里被工具链误解析
_INV = "in" + "voke"
_PAR = "para" + "meter"
_NA = "na" + "me"


def _mk_markup(tool: str, params: list[tuple[str, str]]) -> str:
    """构造一条原生方言标记（分段拼接）。"""
    lines = [f"<{_BAR}calls>", f"<{_BAR}{_INV} {_NA}=\"{tool}\">"]
    for k, v in params:
        lines.append(f"<{_BAR}{_PAR} {_NA}=\"{k}\" string=\"true\">{v}</{_BAR}{_PAR}>")
    lines.append(f"</{_BAR}{_INV}>")
    lines.append(f"</{_BAR}calls>")
    return "\n".join(lines)


def test_parse_native_with_args_param():
    """args 参数块携带 JSON 对象 → 直接作为工具参数。"""
    markup = _mk_markup("invoice_list", [("args", '{"page": 1, "page_size": 20}'), ("name", "invoice_list")])
    calls = _parse_native_tool_calls(markup)
    assert calls is not None and len(calls) == 1
    assert calls[0].name == "invoice_list"
    assert calls[0].args == {"page": 1, "page_size": 20}
    assert calls[0].id  # 唯一 id 已生成


def test_parse_native_with_flat_params():
    """无 args 块时，散装参数按名值组装（值尝试 JSON 解码）。"""
    markup = _mk_markup("invoice_stats", [("month", "2026-10"), ("limit", "5")])
    calls = _parse_native_tool_calls(markup)
    assert calls is not None and calls[0].name == "invoice_stats"
    assert calls[0].args == {"month": "2026-10", "limit": 5}


def test_parse_none_when_absent():
    assert _parse_native_tool_calls("普通的中文回答，没有任何标记") is None
    assert _parse_native_tool_calls('{"tool_call": [{"name": "x", "args": {}}]}') is None


def test_assistant_reply_routes_native_to_tool_calls():
    """_parse_assistant_reply 把方言整段解析成 tool_calls（不再当纯文本透出）。"""
    markup = _mk_markup("invoice_list", [("args", '{"page": 1}')])
    msg = _parse_assistant_reply(markup)
    assert msg.text == ""
    assert len(msg.tool_calls) == 1 and msg.tool_calls[0].name == "invoice_list"


def test_probe_detects_native_prefix():
    """流式探测：标记开头即判 tool_call（不等 256 字符），半截标记等待。"""
    assert _probe_stream_buffer(_DSML_OPEN + "calls>") is True
    assert _probe_stream_buffer("<") is None  # 可能是标记前缀，等更多字符
    assert _probe_stream_buffer(_DSML_OPEN[:4]) is None
    # 普通以 < 开头的文本不受影响（不是标记前缀 → 走既有规则 → 未到阈值等待）
    assert _probe_stream_buffer("<b>加粗</b>") is None


def test_native_markup_serialization_roundtrip_json_guard():
    """保险：构造出的标记里确有分隔串（防止转义写错导致测试自我欺骗）。"""
    markup = _mk_markup("t", [("args", "{}")])
    assert _BAR in markup and json.dumps({"ok": True})  # 第二项仅防御性调用


# ============ 驱动层流式端到端（对应用户可见行为） ============


class _Delta:
    def __init__(self, c):
        self.delta = type("M", (), {"content": c})()


class _Chunk:
    def __init__(self, c):
        self.choices = [_Delta(c)]
        self.usage = None


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kw):
        async def _gen():
            for c in self._chunks:
                yield c

        return _gen()


class _FakeClient:
    def __init__(self, chunks):
        self.chat = type("C", (), {"completions": _FakeCompletions(chunks)})()


async def test_stream_does_not_leak_native_markup():
    """用户可见行为：原子方言标记流式输出时，前端一个字符都不应看到；工具被真正组装。"""
    markup = _mk_markup("invoice_list", [("args", '{"page": 1}'), ("name", "invoice_list")])
    # 任意切片模拟流式分块（含分隔串被切断的情况）
    chunks = [_Chunk(markup[i:i + 17]) for i in range(0, len(markup), 17)]
    driver = OpenAIDriver(_FakeClient(chunks), model="m")
    deltas: list[str] = []
    final = None
    async for kind, payload in driver.stream_chat(system_prompt="s", messages=[], tools=[]):
        if kind == "text_delta":
            deltas.append(payload)
        else:
            final = payload
    assert deltas == [], "原始标记绝不能透出到前端"
    assert final is not None and final.tool_calls
    assert final.tool_calls[0].name == "invoice_list"
    assert final.tool_calls[0].args == {"page": 1}
