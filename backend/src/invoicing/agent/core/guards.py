"""Agent Loop guards: 结构性兜底, 不依赖 LLM 合作.

挂到 run_agent_loop 的 on_turn_end, 返回 True → 立即停 (stop_reason="hook_stop",
具体原因写到 state.stop_reason_detail)。

【设计动机 2026-09-04】
实测发现 agent 在以下情况会陷入"幻觉推理循环":
  - LLM 在文本里写 {"tool_call": [...]} 假装调工具（其实没真发 tool_use）
  - 同一工具连续失败但 LLM 一直重试
  - 工具错误率高但 LLM 还在加 turn
这些情况 LLM 不会自觉停下，必须结构性兜底。

【关闭方式】
环境变量 INVOICING_AGENT_LOOP_GUARD=off 关闭（dev 调试用）。
默认 on。
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from invoicing.agent.core.context import AgentState
    from invoicing.agent.core.tools import ToolContent


# 幻觉 tool_call JSON 检测：LLM 在 text 里写 {"tool_call": ...} 或
# `{"tool_call": [...]}` 这种"假装调工具"的 JSON。code fence 包住的不算。
_HALLUCINATED_TOOL_CALL = re.compile(r'\{\s*"tool_call"\s*:\s*[\[\{]')


def _tool_results(state: "AgentState") -> list[tuple[str, bool, str]]:
    """从 state.ctx.messages 抽出全部 ToolResultMessage.

    Returns:
        [(tool_name, is_error, content_preview), ...] —— 顺序与执行一致.
    """
    out: list[tuple[str, bool, str]] = []
    # 关联 tool_call_id → tool_name：靠前序 AssistantMessage.tool_calls
    id_to_name: dict[str, str] = {}
    from invoicing.agent.core.context import AssistantMessage, ToolResultMessage

    for msg in state.ctx.messages:
        if isinstance(msg, AssistantMessage):
            for tc in msg.tool_calls:
                id_to_name[tc.id] = tc.name
        elif isinstance(msg, ToolResultMessage):
            name = id_to_name.get(msg.tool_call_id, "?")
            content = _content_preview(msg.content)
            out.append((name, msg.is_error, content))
    return out


def _content_preview(blocks: "list[ToolContent]") -> str:
    """tool content → 字符串预览 (前 200 字), 用于观测 / 日志."""
    parts: list[str] = []
    for b in blocks:
        text = getattr(b, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(b))
    s = "\n".join(parts)
    return s[:200]


def _last_assistant_text(state: "AgentState") -> tuple[str, bool] | None:
    """返回 (text, has_real_tool_calls) 最近一次 AssistantMessage 的快照."""
    from invoicing.agent.core.context import AssistantMessage

    for msg in reversed(state.ctx.messages):
        if isinstance(msg, AssistantMessage):
            return msg.text or "", bool(msg.tool_calls)
    return None


_CODE_FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """去掉 ```...``` 块, 避免把 LLM 在示例/解释里的 JSON 当幻觉 tool_call."""
    return _CODE_FENCE_RE.sub("", text)


def capability_guard(state: "AgentState") -> bool:
    """Agent Loop 结构性兜底. 返回 True → 停.

    4 条规则 (任一满足即停):
      R1 连续 2 次同工具失败
      R2 同一工具累计 ≥3 次失败
      R3 LLM 文本含幻觉 tool_call JSON
      R4 工具总错误率 ≥50% 且至少 2 次

    副作用: 停之前把原因写到 state.stop_reason_detail (sse_bridge 透出).
    """
    if os.getenv("INVOICING_AGENT_LOOP_GUARD", "on").lower() in ("off", "0", "false"):
        return False

    results = _tool_results(state)

    # ---- R1 连续 2 次同工具失败 ----
    if len(results) >= 2:
        last2 = results[-2:]
        if (last2[0][1] and last2[1][1] and last2[0][0] == last2[1][0]):
            state.stop_reason_detail = f"consecutive_fail:{last2[-1][0]}"
            return True

    # ---- R2 同一工具累计 ≥3 次失败 ----
    if results:
        err_counter = Counter(name for name, is_err, _ in results if is_err)
        if err_counter:
            top_name, top_count = err_counter.most_common(1)[0]
            if top_count >= 3:
                state.stop_reason_detail = f"repeated_fail:{top_name}({top_count}x)"
                return True

    # ---- R3 LLM 幻觉 tool_call JSON ----
    last = _last_assistant_text(state)
    if last is not None:
        text, has_real_calls = last
        # 去掉 ```...``` code fence 块, 避免误判 "我会发这种格式" 的示例
        text_no_fence = _strip_code_fences(text) if text else ""
        if text_no_fence and _HALLUCINATED_TOOL_CALL.search(text_no_fence) and not has_real_calls:
            state.stop_reason_detail = "hallucinated_tool_call_json"
            return True

    # ---- R4 工具错误率 ≥50% (至少 2 次调用) ----
    total = len(results)
    if total >= 2:
        errs = sum(1 for _, is_err, _ in results if is_err)
        if errs / total >= 0.5:
            state.stop_reason_detail = f"tool_error_rate_high({errs}/{total})"
            return True

    return False


__all__ = ["capability_guard"]
