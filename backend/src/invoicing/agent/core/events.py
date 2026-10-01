"""Agent Loop 事件层次（对齐 pi_agent 的 AgentEvent Union）.

8 层嵌套，从外到里：
    AgentStart
      └─ TurnStart
           └─ MessageStart
                └─ MessageUpdate*   (流式 token / thinking)
                └─ [ToolStart → ToolUpdate* → ToolEnd]*
           └─ TurnEnd (合并到 MessageEnd 一起收，本骨架先不单列)
      └─ AgentEnd

每个事件都有 `type: str` 判别符，方便 SSE 序列化和 UI 消费。用 dataclass 而不是
pydantic：无外部依赖，AuditMind 项目里 dataclass 是常见风格。

序列化：event.to_dict() 返回 JSON-friendly dict（供 SSE emit）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class AgentStartEvent:
    """Agent Loop 启动.

    payload:
        session_id: 关联到 AuditMind conversations 表的会话 id
        max_turns: 本次会话允许的最大轮数（loop 用来兜底）
    """
    session_id: str
    max_turns: int | None = None
    type: str = field(default="agent_start", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurnStartEvent:
    """一个 LLM ↔ Tool 交互周期开始（LLM 说话 → 可能调工具 → 工具回复）."""
    turn_index: int
    type: str = field(default="turn_start", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MessageStartEvent:
    """Assistant 开始生成回复（LLM 调用起始点）."""
    turn_index: int
    type: str = field(default="message_start", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MessageUpdateEvent:
    """流式 token 增量.

    kind:
        "text_delta": 正文增量
        "thinking_delta": 推理模型 thinking 块增量（Qwen3 / o1 / Claude 3.7）
    """
    turn_index: int
    kind: str  # "text_delta" | "thinking_delta"
    delta: str
    type: str = field(default="message_update", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolStartEvent:
    """工具执行开始."""
    turn_index: int
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    type: str = field(default="tool_start", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolUpdateEvent:
    """工具执行中的部分进度（例如 bash 流式 stdout / 长任务进度百分比）."""
    turn_index: int
    tool_call_id: str
    tool_name: str
    delta: str
    type: str = field(default="tool_update", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolEndEvent:
    """工具执行结束.

    is_error=True 表示工具本身抛错（例如文件读不存在），LLM 拿到 error 后会决定重试
    or 换工具 or 告诉用户。这个不算 workflow 失败，Agent Loop 继续。
    """
    turn_index: int
    tool_call_id: str
    tool_name: str
    output: str
    is_error: bool = False
    duration_ms: int | None = None
    type: str = field(default="tool_end", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentEndEvent:
    """Agent Loop 终止.

    reason:
        "no_more_tool_calls": LLM 说完并且不再要求工具 → 正常收尾
        "max_turns": 达到 max_turns 上限，强制停
        "cancelled": 外部 cancel_event 触发
        "hook_stop": on_turn_start / on_turn_end hook 返回 True 请求停止
        "error": 不可恢复错误
    stop_reason_detail:
        2026-09-04 新增 —— hook 停止时具体原因（如 consecutive_fail:get_finding_detail）。
        reason == "hook_stop" 时由 capability_guard 写入，前端可展示给用户。
    """
    turn_index: int
    reason: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    stop_reason_detail: str | None = None
    type: str = field(default="agent_end", init=False)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


AgentEvent = Union[
    AgentStartEvent,
    TurnStartEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolStartEvent,
    ToolUpdateEvent,
    ToolEndEvent,
    AgentEndEvent,
]
