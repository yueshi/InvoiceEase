"""agent core 移植层：事件序列化 / 消息家族 / guard 规则 / JSON 容错提取。"""
import asyncio
from invoicing.agent.core.context import (
    AgentContext, AgentState, AssistantMessage, ToolCall, ToolResultMessage, UserMessage,
)
from invoicing.agent.core.events import AgentEndEvent, ToolEndEvent
from invoicing.agent.core.guards import capability_guard
from invoicing.agent.core.tools import AgentToolResult, TextContent
from invoicing.agent.json_extract import extract_json_dict


def test_event_to_dict():
    ev = ToolEndEvent(turn_index=1, tool_call_id="tc-1", tool_name="invoice_list",
                      output="ok", is_error=False, duration_ms=42)
    d = ev.to_dict()
    assert d["type"] == "tool_end" and d["tool_name"] == "invoice_list" and d["duration_ms"] == 42
    end = AgentEndEvent(turn_index=1, reason="no_more_tool_calls")
    assert end.to_dict()["reason"] == "no_more_tool_calls"


def test_message_family():
    m = AssistantMessage(text="hi", tool_calls=[ToolCall(id="1", name="t", args={})])
    assert m.role == "assistant" and m.tool_calls[0].name == "t"
    r = ToolResultMessage(tool_call_id="1", content=[TextContent(text="out")])
    assert r.role == "tool_result" and r.content[0].text == "out"


def test_extract_json_dict_prefers_outer():
    text = '前缀废话 {"tool_call": [{"name": "invoice_list", "args": {}}]} 后缀'
    data = extract_json_dict(text)
    assert data is not None and data["tool_call"][0]["name"] == "invoice_list"
    assert extract_json_dict("纯文本没有 JSON") is None


def test_guard_r1_consecutive_fail():
    """R1：同一工具连续 2 次失败 → 停，并写 stop_reason_detail。"""
    ctx = AgentContext()
    state = AgentState(ctx=ctx)
    ctx.messages.append(AssistantMessage(text="", tool_calls=[ToolCall(id="a", name="x", args={})]))
    ctx.messages.append(ToolResultMessage(tool_call_id="a", content=[TextContent(text="err")], is_error=True))
    ctx.messages.append(AssistantMessage(text="", tool_calls=[ToolCall(id="b", name="x", args={})]))
    ctx.messages.append(ToolResultMessage(tool_call_id="b", content=[TextContent(text="err2")], is_error=True))
    assert capability_guard(state) is True
    assert state.stop_reason_detail == "consecutive_fail:x"


def test_guard_r3_hallucinated_tool_call():
    """R3：文本里写 tool_call JSON 但没真发 tool_calls → 幻觉，停。"""
    ctx = AgentContext()
    state = AgentState(ctx=ctx)
    ctx.messages.append(AssistantMessage(text='我来查：{"tool_call": [{"name": "invoice_list"}]}'))
    assert capability_guard(state) is True
    assert state.stop_reason_detail == "hallucinated_tool_call_json"
