# test/test_agent_timeline.py
"""助手消息时间线：桥按事件到达顺序折叠 blocks（相邻同类合并）+ 落库读回。"""
from invoicing.agent.core.events import (
    AgentEndEvent,
    MessageUpdateEvent,
    ToolEndEvent,
    ToolStartEvent,
)
from invoicing.agent.core.sse_bridge import bridge_events_to_sse
from invoicing.agent.observability import record_turn
from invoicing.agent.sse import AgentSSEEmitter
from invoicing.models import AgentMessage, AgentSession, User


async def test_bridge_builds_interleaved_blocks():
    """思考 → 工具 A → 思考 → 工具 B(失败) → 正文：5 块且顺序精确。"""
    em = AgentSSEEmitter()
    stats: list[dict] = []
    blocks: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="想1")
        yield ToolStartEvent(turn_index=0, tool_call_id="1", tool_name="invoice_stats", args={})
        yield ToolEndEvent(turn_index=0, tool_call_id="1", tool_name="invoice_stats",
                           output="ok", is_error=False, duration_ms=15)
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="想2")
        yield ToolStartEvent(turn_index=0, tool_call_id="2", tool_name="invoice_list", args={})
        yield ToolEndEvent(turn_index=0, tool_call_id="2", tool_name="invoice_list",
                           output="boom", is_error=True)
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="答")
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    await bridge_events_to_sse(_events(), em, stats, blocks)
    assert blocks == [
        {"type": "reasoning", "text": "想1"},
        {"type": "tool", "tool": "invoice_stats", "status": "done", "ms": 15},
        {"type": "reasoning", "text": "想2"},
        {"type": "tool", "tool": "invoice_list", "status": "failed", "ms": None},
        {"type": "text", "text": "答"},
    ]
    # 旧字段路径不受影响
    assert stats[1] == {"tool": "invoice_list", "status": "failed", "ms": None}


async def test_bridge_merges_adjacent_same_kind():
    """相邻同类合并：thinking A+B 一块、text C+D 一块（工具块不参与合并）。"""
    em = AgentSSEEmitter()
    blocks: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="A")
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="B")
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="C")
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="D")
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    await bridge_events_to_sse(_events(), em, [], blocks)
    assert blocks == [
        {"type": "reasoning", "text": "AB"},
        {"type": "text", "text": "CD"},
    ]


async def test_bridge_three_arg_call_still_works():
    """旧调用方只传 3 参：不建时间线也不报错。"""
    em = AgentSSEEmitter()
    stats: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="你")
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    await bridge_events_to_sse(_events(), em, stats)
    assert stats == []


def test_record_turn_persists_blocks(db):
    """blocks 落库并读回；content/tool_calls 旧字段照写。"""
    user = User(username="tl_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()

    blocks = [
        {"type": "reasoning", "text": "想"},
        {"type": "tool", "tool": "invoice_stats", "status": "done", "ms": 15},
        {"type": "text", "text": "答"},
    ]
    record_turn(db, session_id=s.id, user_id=user.id, user_message="查",
                assistant_reply="答",
                tool_calls=[{"tool": "invoice_stats", "status": "done", "ms": 15}],
                input_tokens=1, output_tokens=2, duration_ms=3, error_code=None,
                blocks=blocks)
    db.expire_all()
    m = db.query(AgentMessage).filter_by(session_id=s.id, role="assistant").one()
    assert m.blocks == blocks
    assert m.content == "答" and m.tool_calls[0]["tool"] == "invoice_stats"
