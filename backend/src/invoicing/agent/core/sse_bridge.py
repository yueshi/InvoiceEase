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


def _append_block(blocks: list[dict], block: dict) -> None:
    """向时间线追加块：reasoning/text 相邻同类合并文本，其余直接入列。"""
    tail = blocks[-1] if blocks else None
    if tail is not None and tail["type"] == block["type"] and block["type"] in ("reasoning", "text"):
        tail["text"] += block["text"]
        return
    blocks.append(block)


async def bridge_events_to_sse(
    events: AsyncIterator[AgentEvent],
    emitter,
    tool_calls_stats: list[dict],
    blocks: list[dict] | None = None,
) -> AgentEndEvent | None:
    """消费 AgentEvent 流，转发到 emitter；返回最终 AgentEndEvent（如有）.

    映射规则：
        MessageUpdate  → emit("token", {"text": delta})      text_delta 走正文
                       → emit("reasoning", {"text": delta})  thinking_delta 走思考过程（实时展示，不落库）
        ToolStart      → emit("tool_call", {"tool": ..., "status": "start"})
        ToolEnd        → emit("tool_call", {"tool": ..., "status": "done"|"failed", "ms": ...})
        AgentStart/TurnStart/MessageStart/AgentEnd  → 内部观测用，不 emit

    blocks（可选）：同一批事件按到达顺序折叠出的持久化时间线（思考/工具/文本，相邻同类合并）。
    第三参调用方兼容——不传则不建时间线。
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
                if blocks is not None:
                    _append_block(blocks, {"type": "text", "text": ev.delta})
            elif ev.kind == "thinking_delta" and ev.delta:
                # 思考过程：前端独立折叠块实时展示（纯文本渲染），不落库、不进正文
                emitter.emit("reasoning", {"text": ev.delta})
                if blocks is not None:
                    _append_block(blocks, {"type": "reasoning", "text": ev.delta})
            continue
        if isinstance(ev, ToolStartEvent):
            emitter.emit("tool_call", {"tool": ev.tool_name, "status": "start"})
            if blocks is not None:
                blocks.append({"type": "tool", "tool": ev.tool_name, "status": "start", "ms": None})
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
            if blocks is not None:
                # 复用既有匹配语义：最近一个同名且仍 start 的工具块
                for b in reversed(blocks):
                    if b["type"] == "tool" and b["tool"] == ev.tool_name and b["status"] == "start":
                        b["status"] = status
                        b["ms"] = ev.duration_ms
                        break
            continue
        if isinstance(ev, AgentEndEvent):
            end_event = ev
            break
    return end_event


__all__ = ["bridge_events_to_sse", "last_assistant_text"]
