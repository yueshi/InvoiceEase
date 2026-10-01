"""Agent Loop 上下文与消息类型.

对齐 pi_agent.types 的 AgentContext + AgentState + AgentMessage 家族，
但去掉 pi_ai 侧的依赖，用纯 dataclass。

消息家族 3 种：
    UserMessage      —— 用户输入
    AssistantMessage —— LLM 输出（可能含 tool_calls）
    ToolResultMessage —— 工具执行结果（回到 LLM 视野）

AgentContext = 静态输入（system prompt + tools 目录 + 历史消息）
AgentState = 动态运行时（is_streaming, pending_tool_calls, error, usage 累计）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from invoicing.agent.core.tools import AgentTool, ToolContent


# ============ 工具调用 ============


@dataclass(frozen=True)
class ToolCall:
    """LLM 发起的一次工具调用请求.

    id: LLM 生成的调用 id（后续 ToolResultMessage 通过它引用回来）
    name: 工具名
    args: 参数 dict
    """
    id: str
    name: str
    args: dict[str, Any]


# ============ 消息家族 ============


@dataclass
class UserMessage:
    """用户消息（进入 Agent Loop 的起点）."""
    content: str
    role: str = field(default="user", init=False)


@dataclass
class AssistantMessage:
    """LLM 输出.

    text: 纯文本部分（可能为空——LLM 只发工具调用时）
    tool_calls: LLM 请求的工具调用列表（可能多个，Loop 会并行执行）
    thinking: 推理块（Qwen3 / o1 / Claude 3.7）——观测记录用，不回填给 LLM
    usage: {"prompt_tokens": int, "completion_tokens": int}——streaming 收官时填入
    """
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    role: str = field(default="assistant", init=False)


@dataclass
class ToolResultMessage:
    """工具执行结果，回填给 LLM.

    tool_call_id: 对应哪个 ToolCall（AssistantMessage.tool_calls[i].id）
    content: 工具输出的 ContentBlock 列表
    is_error: 工具执行失败标记
    """
    tool_call_id: str
    content: list[ToolContent]
    is_error: bool = False
    role: str = field(default="tool_result", init=False)


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


# ============ Agent 上下文 ============


@dataclass
class AgentContext:
    """一轮 Agent Loop 的输入配置.

    system_prompt: LLM 系统提示（工具目录会自动追加）
    messages: 历史消息列表（loop 里持续追加）
    tools: 可用工具目录（name → AgentTool 或直接一个 list，看 loop 实现偏好）
    max_turns: 单次会话 loop 最大轮数（防死循环）
    metadata: 会话级附加信息（project_id / page 等 AuditMind 语义）
    """
    system_prompt: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)
    max_turns: int = 10
    metadata: dict[str, Any] = field(default_factory=dict)


# ============ Agent 运行时状态 ============


@dataclass
class AgentState:
    """Agent Loop 执行期间的动态状态（context 的超集）.

    ctx 是纯配置；state 记录 loop 正在做什么。
    Loop 停止时可以通过 state 快照回看：走了几轮、什么原因停、总 tokens。
    """
    ctx: AgentContext
    turn_index: int = 0
    is_streaming: bool = False
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # 2026-09-04: loop hook 填的停止原因细分 (如 "consecutive_fail:get_finding_detail")
    # 透到 AgentEndEvent 让前端 / 观测拿到具体原因
    stop_reason_detail: str | None = None


__all__ = [
    "ToolCall",
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "Message",
    "AgentContext",
    "AgentState",
]
