"""AgentEvent → 现有 SSE 契约转换 helper.

多个 workflow 都需要把 Agent Loop 出的事件转成 emit("token")/emit("tool_call") 的
老 SSE 格式（保持前端 useAgentStream 不改）。抽出来 DRY。
"""
from __future__ import annotations

from typing import AsyncIterator

from invoicing.agent.core.context import AssistantMessage
from invoicing.agent.core.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolEndEvent,
    ToolStartEvent,
    TurnStartEvent,
)


def last_assistant_text(messages) -> str:
    """从 messages 尾部找最近一条 AssistantMessage 的 text（有 tool_calls 的中间态跳过）.

    P5 用于从 loop 结束后的 ctx.messages 提取 final reply，写回 conversations 表。
    """
    for m in reversed(messages):
        if isinstance(m, AssistantMessage) and m.text and not m.tool_calls:
            return m.text
    return ""


async def bridge_events_to_sse(
    events: AsyncIterator[AgentEvent],
    emitter,
    tool_calls_stats: list[dict],
) -> AgentEndEvent | None:
    """消费 AgentEvent 流，转发到 emitter；返回最终 AgentEndEvent（如有）.

    映射规则：
        MessageUpdate  → emit("token", {"text": delta})      text_delta 走正文
                       → emit("reasoning", {"text": delta})  thinking_delta 走思考过程（实时展示，不落库）
        ToolStart      → emit("tool_call", {"tool": ..., "status": "start"})
        ToolEnd        → emit("tool_call", {"tool": ..., "status": "done"|"failed", "ms": ...})
        AgentStart/TurnStart/MessageStart/AgentEnd  → 内部观测用，不 emit
    """
    end_event: AgentEndEvent | None = None
    async for ev in events:
        if isinstance(ev, AgentStartEvent):
            continue
        if isinstance(ev, TurnStartEvent):
            continue
        if isinstance(ev, MessageStartEvent):
            continue
        if isinstance(ev, MessageUpdateEvent):
            if ev.kind == "text_delta" and ev.delta:
                emitter.emit("token", {"text": ev.delta})
            elif ev.kind == "thinking_delta" and ev.delta:
                # 思考过程：前端独立折叠块实时展示（纯文本渲染），不落库、不进正文
                emitter.emit("reasoning", {"text": ev.delta})
            continue
        if isinstance(ev, ToolStartEvent):
            emitter.emit("tool_call", {"tool": ev.tool_name, "status": "start"})
            continue
        if isinstance(ev, ToolEndEvent):
            status = "failed" if ev.is_error else "done"
            emitter.emit("tool_call", {
                "tool": ev.tool_name,
                "status": status,
                "ms": ev.duration_ms,
            })
            tool_calls_stats.append({
                "tool": ev.tool_name,
                "status": status,
                "ms": ev.duration_ms,
            })
            continue
        if isinstance(ev, AgentEndEvent):
            end_event = ev
            break
    return end_event


__all__ = ["bridge_events_to_sse", "last_assistant_text"]
