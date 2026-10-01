"""AgentTool 协议 + 结果类型（对齐 pi_agent.types.AgentTool + AgentToolResult）.

设计要点：
- Duck typing（Protocol），不是强制基类。AuditMind 现有工具（find_callers 等）
  只要凑齐 name / description / schema / execute 就能满足。
- execute 是 async，参数含 cancel_event（asyncio.Event）和 update_callback，
  分别支持外部中断和流式部分进度。
- 结果只用 TextContent / ImageContent 两种块——够我们用了。（pi_agent 里还有
  ToolUseContent 等更复杂的东西，那些是给 Claude computer use 用的，先不引入。）
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Union, runtime_checkable


# ============ 内容块 ============


@dataclass(frozen=True)
class TextContent:
    """文本块——工具输出的默认形态."""
    text: str
    type: str = field(default="text", init=False)


@dataclass(frozen=True)
class ImageContent:
    """图片块（base64 编码）——例如截图工具、图形化 report 预览."""
    data: str  # base64 encoded
    mime_type: str  # e.g. "image/png"
    type: str = field(default="image", init=False)


ToolContent = Union[TextContent, ImageContent]


# ============ 工具执行结果 ============


@dataclass
class AgentToolResult:
    """工具执行返回值.

    - content: 一或多块内容（LLM 会依次看到）
    - is_error: 工具自身出错（例如文件不存在、SQL 语法错），LLM 会根据这个决定
      是重试、换参数、还是告诉用户。不算 Agent Loop 失败。
    - metadata: 观测用附加信息（例如 SQL 执行耗时、命中行数），不进 LLM 视野
    """
    content: list[ToolContent]
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text(cls, text: str, is_error: bool = False, **metadata) -> "AgentToolResult":
        """快捷构造：单文本块."""
        return cls(content=[TextContent(text=text)], is_error=is_error, metadata=metadata)


# ============ 更新回调 ============


ToolUpdateCallback = Callable[[str], Awaitable[None]]
"""工具执行中上报部分进度（bash 流式 stdout / 长任务百分比等）.

工具内部 `await update_callback("...")` 即会触发一个 ToolUpdateEvent。
Loop 层保证这个 callback 是异步安全的（内部走 asyncio.Queue）。
"""


# ============ 工具协议 ============


@runtime_checkable
class AgentTool(Protocol):
    """一个工具最小要素. 对现有 AuditMind 工具只是"凑够这几个属性"即可.

    属性:
        name: 工具唯一名字，LLM 用来 dispatch（例如 "find_callers"）
        description: 一句话描述，进 LLM system prompt 的工具目录
        schema: JSON Schema，描述 args 结构

    方法:
        execute: 异步执行，返回 AgentToolResult

    execute 签名:
        async def execute(
            self,
            tool_call_id: str,        # 本次调用的唯一 id（来自 LLM 的 tool_call）
            args: dict,               # LLM 生成的参数
            cancel_event: asyncio.Event,  # 外部中断信号
            update_callback: ToolUpdateCallback,  # 部分进度上报
        ) -> AgentToolResult
    """

    name: str
    description: str
    schema: dict[str, Any]

    async def execute(
        self,
        tool_call_id: str,
        args: dict[str, Any],
        cancel_event: asyncio.Event,
        update_callback: ToolUpdateCallback,
    ) -> AgentToolResult:
        ...


__all__ = [
    "TextContent",
    "ImageContent",
    "ToolContent",
    "AgentToolResult",
    "ToolUpdateCallback",
    "AgentTool",
]
