# backend/src/invoicing/agent/main_agent.py
"""MainAgent：发票易 Web 助手统一入口（移植 AuditMind R1 重构版）。

职责：组装 Loop 上下文 → 跑 run_agent_loop（经 SSE 桥转前端事件）→ 返回观测统计。
不落库（落库由 API 层用独立 session 调 observability.record_turn）。
"""
import time

from invoicing.agent.core.context import AgentContext as LoopCtx, AgentState, UserMessage
from invoicing.agent.core.driver import OpenAIDriver
from invoicing.agent.core.guards import capability_guard
from invoicing.agent.core.loop import run_agent_loop
from invoicing.agent.core.sse_bridge import bridge_events_to_sse, last_assistant_text
from invoicing.agent.llm import agent_model
from invoicing.agent.system_prompts import build_system_prompt
from invoicing.config import settings
from invoicing.schemas.agent import AgentContext

_STATIC_FALLBACK = """我可以帮你做这些事（当前未启用大模型，以下为功能说明）：

1. **发票查询**：告诉我时间范围/状态/关键词，例如"列出本月待复核的发票"
2. **报销单**：从可报销票池选票建单、加票、提交审批
3. **银行回单**：查回单清单、对账汇报、手动配对
4. **报表**：月度成本报表、健康报告

启用大模型（INVOICING_LLM_ENABLED=true + API Key）后我就能直接执行这些操作。
"""


async def run_agent(
    *,
    message: str,
    context: AgentContext,
    emitter,
    history,
    tools,
    session_id: str,
    client,
    cancel_event=None,
) -> dict:
    """跑主 Agent，返回观测统计（shape 供 record_turn 使用）。"""
    started = time.perf_counter()

    # LLM 不可用 → 静态能力说明（分片发，保留打字机体感）
    if client is None:
        for i in range(0, len(_STATIC_FALLBACK), 20):
            emitter.emit("token", {"text": _STATIC_FALLBACK[i:i + 20]})
        emitter.emit("done", {"total_ms": 0, "tool_count": 0})
        return {
            "tool_calls": [], "input_tokens": 0, "output_tokens": 0,
            "duration_ms": 0, "error_code": None, "assistant_reply": _STATIC_FALLBACK,
            "blocks": [{"type": "text", "text": _STATIC_FALLBACK}],
        }

    # 组装 loop 上下文（历史在前，当前轮带 [用户消息]/[页面上下文] 包装）
    user_content = _format_user_message(message, context)
    loop_ctx = LoopCtx(
        system_prompt=build_system_prompt(settings.agent_max_steps),
        messages=[*history, UserMessage(content=user_content)],
        tools=list(tools),
        max_turns=settings.agent_max_steps,
        metadata={"page": context.page},
    )
    state = AgentState(ctx=loop_ctx)

    driver = OpenAIDriver(
        client, model=agent_model(),
        temperature=settings.agent_llm_temperature,
        max_tokens=settings.agent_max_tokens,
    )
    tool_calls_stats: list[dict] = []
    blocks: list[dict] = []  # 有序时间线（思考/工具/文本），随事件到达顺序折叠
    error_code: str | None = None

    try:
        await bridge_events_to_sse(
            run_agent_loop(
                driver, state, session_id=session_id,
                cancel_event=cancel_event, on_turn_end=capability_guard,
            ),
            emitter, tool_calls_stats, blocks,
        )
    except Exception as e:
        error_code = "AGENT_EXCEPTION"
        emitter.emit("error", {"code": error_code, "message": str(e), "retryable": True})

    total_ms = int((time.perf_counter() - started) * 1000)
    emitter.emit("done", {
        "total_ms": total_ms,
        "tool_count": len(tool_calls_stats),
        "stop_reason_detail": state.stop_reason_detail,
    })

    history_len = len(history)
    return {
        "tool_calls": tool_calls_stats,
        "blocks": blocks,
        "input_tokens": state.total_input_tokens,
        "output_tokens": state.total_output_tokens,
        "duration_ms": total_ms,
        "error_code": error_code,
        # 只在本轮消息里找 final（切片跳过历史）——否则空回复时会回捞上一轮答复当本轮结果
        "assistant_reply": last_assistant_text(loop_ctx.messages[history_len:]),
    }


def _format_user_message(message: str, context: AgentContext) -> str:
    """用户原话 + 页面上下文打包。

    `[用户消息]` 前缀是 system prompt「只看最后一条 [用户消息]」判据的唯一锚点：
    历史轮走裸文本（DB 存的就是原文），只有当前轮带包装。
    """
    lines = [f"[用户消息]\n{message}\n"]
    bits = [f"page={context.page}"]
    if context.invoice_id:
        bits.append(f"invoice_id={context.invoice_id}")
    if context.claim_id:
        bits.append(f"claim_id={context.claim_id}")
    if context.receipt_id:
        bits.append(f"receipt_id={context.receipt_id}")
    if context.month:
        bits.append(f"month={context.month}")
    lines.append(f"\n[页面上下文]\n{', '.join(bits)}")
    return "\n".join(lines)
