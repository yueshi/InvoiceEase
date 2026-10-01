# test/test_agent_sse.py
"""SSE emitter 帧契约 + 事件桥折叠。"""
import pytest
from invoicing.agent.core.context import AssistantMessage
from invoicing.agent.core.events import (
    AgentEndEvent, MessageUpdateEvent, ToolEndEvent, ToolStartEvent,
)
from invoicing.agent.core.sse_bridge import bridge_events_to_sse, last_assistant_text
from invoicing.agent.sse import AgentSSEEmitter


async def test_emitter_frames_roundtrip():
    em = AgentSSEEmitter()
    em.emit("token", {"text": "你好"})
    em.emit("tool_call", {"tool": "invoice_list", "status": "start"})
    em.close()
    frames = [f async for f in em.stream()]
    assert frames[0] == 'data: {"type": "token", "data": {"text": "你好"}}\n\n'
    assert '"tool_call"' in frames[1]
    assert len(frames) == 2


def test_emitter_rejects_unknown_type():
    em = AgentSSEEmitter()
    with pytest.raises(ValueError):
        em.emit("intent", {})


async def test_bridge_folds_events():
    em = AgentSSEEmitter()
    stats: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="你")
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="想")  # 不透出
        yield ToolStartEvent(turn_index=0, tool_call_id="1", tool_name="invoice_list", args={})
        yield ToolEndEvent(turn_index=0, tool_call_id="1", tool_name="invoice_list",
                           output="ok", is_error=False, duration_ms=8)
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    end = await bridge_events_to_sse(_events(), em, stats)
    em.close()
    frames = [f async for f in em.stream()]
    assert len(frames) == 3  # token + tool_start + tool_end（thinking 与内部事件不透出）
    assert end is not None and end.reason == "no_more_tool_calls"
    assert stats == [{"tool": "invoice_list", "status": "done", "ms": 8}]


def test_last_assistant_text_skips_tool_turns():
    msgs = [
        AssistantMessage(text="", tool_calls=[]),
        AssistantMessage(text="中间态"),
        AssistantMessage(text="最终回答"),
    ]
    assert last_assistant_text(msgs) == "最终回答"
