# test/test_agent_driver.py
"""OpenAIDriver：流式探测（tool_call vs final）、usage、空响应兜底、非流式。"""
import pytest
from invoicing.agent.core.driver import OpenAIDriver, _probe_stream_buffer
from invoicing.agent.core.context import AssistantMessage


class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _Msg:
    def __init__(self, content):
        self.content = content


class _Delta:
    def __init__(self, content):
        self.delta = _Msg(content)


class _Chunk:
    def __init__(self, content=None, usage=None):
        self.choices = [_Delta(content)] if content is not None else []
        self.usage = usage


class _NonStreamResp:
    def __init__(self, content, usage=None):
        class _Choice:
            def __init__(self, m): self.message = m
        self.choices = [_Choice(_Msg(content))]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, chunks=None, content=None, usage=None):
        self._chunks = chunks or []
        self._content = content
        self._usage = usage
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            async def _gen():
                for c in self._chunks:
                    yield c
            return _gen()
        return _NonStreamResp(self._content, self._usage)


class _FakeClient:
    def __init__(self, **kw):
        self.chat = type("C", (), {"completions": _FakeCompletions(**kw)})()


async def _collect(driver, **kw):
    out = []
    async for item in driver.stream_chat(system_prompt="sys", messages=[], tools=kw.get("tools", [])):
        out.append(item)
    return out


async def test_final_text_streams_deltas():
    client = _FakeClient(chunks=[_Chunk("你好，"), _Chunk("这是回答"), _Chunk(usage=_Usage(5, 7))])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    deltas = [p for k, p in out if k == "text_delta"]
    assert "".join(deltas) == "你好，这是回答"
    msg = out[-1][1]
    assert isinstance(msg, AssistantMessage) and msg.text == "你好，这是回答"
    assert msg.usage == {"prompt_tokens": 5, "completion_tokens": 7}


async def test_bare_json_is_tool_call_no_text_leak():
    payload = '{"tool_call": [{"name": "invoice_list", "args": {"page": 1}}]}'
    client = _FakeClient(chunks=[_Chunk(payload)])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    deltas = [p for k, p in out if k == "text_delta"]
    assert deltas == []  # tool_call 路径不泄漏 JSON 给前端
    msg = out[-1][1]
    assert msg.tool_calls[0].name == "invoice_list" and msg.tool_calls[0].args == {"page": 1}


async def test_nl_preamble_then_json_still_tool_call():
    """Qwen 常在 JSON 前垫一句自然语言——必须仍判 tool_call（否则工具从不执行）。"""
    client = _FakeClient(chunks=[_Chunk("我先查一下。\n"), _Chunk('{"tool_call": [{"name": "invoice_list", "args": {}}]}')])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    assert [p for k, p in out if k == "text_delta"] == []
    assert out[-1][1].tool_calls[0].name == "invoice_list"


async def test_empty_stream_fallback_text():
    client = _FakeClient(chunks=[])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    assert out[-1][1].text == "（LLM 无输出）"


async def test_non_stream_chat_parses_usage():
    client = _FakeClient(content="直接回答", usage=_Usage(3, 4))
    driver = OpenAIDriver(client, model="m")
    msg = await driver.chat(system_prompt="sys", messages=[], tools=[])
    assert msg.text == "直接回答" and msg.usage["prompt_tokens"] == 3


def test_probe_buffer_rules():
    assert _probe_stream_buffer("{") is True
    assert _probe_stream_buffer("```json\n{}") is True
    assert _probe_stream_buffer("```chart-pie\n{}") is False
    assert _probe_stream_buffer("普通短文") is None          # 未到判定阈值
    assert _probe_stream_buffer("长" * 300) is False          # 超过 _NL_FINAL_CHARS → final
