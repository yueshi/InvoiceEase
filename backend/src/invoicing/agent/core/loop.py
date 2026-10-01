"""Agent Loop 主循环：async generator，出 AgentEvent 流.

对齐 pi_agent.agent_loop._run_loop 的控制流：
    while True:
        LLM 出 AssistantMessage
        没有 tool_calls → break（no_more_tool_calls）
        并行执行每个 tool_call → 得到 ToolResultMessage
        append 到历史 → 下一轮
        max_turns / cancel_event 兜底

只出事件不落库/emit，SSE 转换由 workflow 层做（保持 core 层无副作用）。
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import AsyncGenerator, Awaitable, Callable, Union

from invoicing.agent.core.context import (
    AgentState,
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from invoicing.agent.core.driver import LLMDriver
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
from invoicing.agent.core.tools import AgentTool, AgentToolResult, TextContent

# 每轮边界回调：返回 True 表示请求停止（stop_reason="hook_stop"）。
# sync / async 均可（返回值经 inspect.isawaitable 判断）。
TurnHook = Callable[[AgentState], Union[bool, Awaitable[bool]]]


# ============ 收尾轮（final answer turn） ============
#
# 【2026-09-22 事故】主循环以 max_turns / hook_stop 结构性强停时，末条 assistant 消息
# 带着 tool_calls、text 为空 → sse_bridge.last_assistant_text() 取不到东西 → 前端一个
# token 都没收到（用户看到工具列表后永久"思考中"）、DB agent_conversations.assistant_reply
# 落 NULL。实证 data/SXDL-master.db id=25：用户问「帮我重新审核这个 Finding，忽略已有
# judge」，8 次工具 / 6 轮用满 / 99s / reply=NULL。
#
# 修法：强停后若本次 run 从没产出过用户可见的 final 文本，就用 tools=[] 再调一次 LLM，
# 逼它拿已有信息给结论。这是"用户发起的对话必须有个答复"的兜底，不是可选项。

# 收尾指令。要点：
#   1) 显式宣告"工具已关闭" —— 光靠 tools=[] 不够：profile 的 system prompt 自己就写了
#      一整段【工具调用协议】（见 main_agent._DEFAULT_SYSTEM_PROMPT），不说清楚模型会继续
#      吐 {"tool_call": ...}，收尾轮的 output 又变成空文本 → 静默复现。
#   2) 禁止"接下来打算查什么"这种过渡话 —— 用户要的是结论。
WRAPUP_INSTRUCTION = (
    "[系统提示] 本次调查到此为止：工具已关闭，不要再调用任何工具"
    "（即使你输出 tool_call，也不会被执行）。\n"
    "现在直接给出最终答复：\n"
    "- 不要再输出 {\"tool_call\": ...} 或任何 JSON 形式的工具调用；\n"
    "- 不要再讲\"接下来打算查什么\"；\n"
    "- 就基于上面已经拿到的对话与工具结果回答用户的问题：结论 + 依据 + 还缺什么；\n"
    "- 信息确实不够时直接说明需要用户补充什么，不要编造字段值。"
)

# 收尾轮仍然没有文本（又吐 tool_call JSON / 吐空 / 调用失败）时的可见兜底。
# 宁可给一句"没结论"，也不能再让前端空着。
WRAPUP_FALLBACK_TEXT = (
    "（本轮调查已中止，模型没有给出结论。可以换个更具体的问法，或让我只查其中一步。）"
)


async def run_agent_loop(
    driver: LLMDriver,
    state: AgentState,
    session_id: str,
    cancel_event: asyncio.Event | None = None,
    on_turn_start: TurnHook | None = None,
    on_turn_end: TurnHook | None = None,
    *,
    final_answer_turn: bool = True,
) -> AsyncGenerator[AgentEvent, None]:
    """跑 Agent Loop 直到 LLM 不再要求工具 / max_turns / cancel / hook 停止.

    Args:
        driver: LLM 客户端（chat -> AssistantMessage）
        state: 承载 ctx + turn_index + usage 累计
        session_id: 观测用；透传给 AgentStartEvent
        cancel_event: 外部中断信号；未提供则内部 new 一个（工具用得到）。
            鸭子类型：任何有 ``is_set()`` 的对象都行（threading.Event 也可）。
        on_turn_start: 每轮 LLM 调用前（turn_index 赋值后、TurnStartEvent 前）触发；
            返回 True → stop_reason="hook_stop" 终止。可在其中向 ctx.messages 注入
            本轮上下文（如 recon 的黑板快照）。
        on_turn_end: 每轮工具结果 append 后触发；返回 True 同上终止。
        final_answer_turn: 结构性强停（max_turns / hook_stop）时，若本次 run 从没产出
            过用户可见的 final 文本，是否补一轮"不带工具"的 LLM 调用逼出结论。
            默认 True（见 WRAPUP_INSTRUCTION 的事故说明）。
            **结构化协议调用方必须传 False** —— recon 的 ReAct 循环
            （auditmind/recon/explorer.py）多一次自由文本调用会让 ReActDriver 多发
            thought/react_end trace、消耗 budget 却不走 add_turn，trace 与记账双漂移。

    Yields:
        AgentEvent（8 层事件）
    """
    if cancel_event is None:
        cancel_event = asyncio.Event()

    ctx = state.ctx
    tools_by_name: dict[str, AgentTool] = {t.name: t for t in ctx.tools}

    yield AgentStartEvent(session_id=session_id, max_turns=ctx.max_turns)

    stop_reason = "no_more_tool_calls"
    # 本次 run 是否产出过"用户可见的 final 文本"。判据与 sse_bridge.last_assistant_text
    # 逐字一致：text 非空 且 无 tool_calls（带 tool_calls 的中间态会被 last_assistant_text
    # 跳过，所以不算）。收尾轮据此决定要不要补。
    #
    # ⚠️ 不要把判据"聪明化"成"文本里含 tool_call JSON 就不算可见"：cap-005 的 mock 返回
    # text='我重试一下:\n{"tool_call":...}' + tool_calls=[]，guard R3 在同一轮命中，
    # 测试断言 driver.call_count == 1 —— 判据放宽/收紧都会破。
    saw_final_text = False

    for turn in range(ctx.max_turns):
        if cancel_event.is_set():
            stop_reason = "cancelled"
            break

        state.turn_index = turn
        if on_turn_start is not None and await _run_hook(on_turn_start, state):
            # hook 请求停止（如 recon 预算耗尽 / 取消）——不发悬空 TurnStartEvent
            stop_reason = "hook_stop"
            break
        yield TurnStartEvent(turn_index=turn)
        yield MessageStartEvent(turn_index=turn)

        # 1. 调 LLM（helper 内部兼容 stream_chat / 只有 .chat 的 mock，并累计 usage）
        out: dict = {}
        async for ev in _call_llm_once(driver, state, turn, ctx.tools, out):
            yield ev
        assistant_msg: AssistantMessage | None = out["msg"]

        if out["error"] is not None:
            state.error = out["error"]
            stop_reason = "error"
            yield MessageUpdateEvent(turn_index=turn, kind="text_delta", delta=f"[LLM 调用失败] {out['error']}")
            break

        if assistant_msg is None:
            # 流意外中断（未发出 assistant_message）
            state.error = "stream ended without assistant_message"
            stop_reason = "error"
            break

        # 1.1 可见 final 文本标志。
        # 位置关键：必须在 1.3 的 guard 之前 —— on_turn_end 在 `if not tool_calls` 早退
        # 之前触发，所以 hook_stop 完全可能发生在"该轮有文本"时（如 R3 幻觉 JSON 那轮），
        # 那种情况已经作答过了，不该再补收尾轮（否则重复作答）。
        if (assistant_msg.text or "").strip() and not assistant_msg.tool_calls:
            saw_final_text = True

        # 1.2 append 到历史
        ctx.messages.append(assistant_msg)

        # 1.3 guard 早检: 即使没真 tool_calls 也要让 guard 跑一遍
        #   - R3 幻觉 tool_call JSON 必须在"无 tool_calls 早退"前检测
        #   - R1/R2/R4 累积规则在后续 turn 也会再调 (工具结果入历史后)
        # hook 名仍叫 on_turn_end 但语义上其实是"每轮 LLM 决策后必跑"
        if on_turn_end is not None and await _run_hook(on_turn_end, state):
            # hook 请求停止（如 capability_guard 命中）—— LLM 输出已入历史
            stop_reason = "hook_stop"
            break

        # 2. 无 tool_calls → 结束
        if not assistant_msg.tool_calls:
            stop_reason = "no_more_tool_calls"
            break

        # 3. 并行执行所有 tool_calls
        results = await _execute_tools_parallel(
            calls=assistant_msg.tool_calls,
            tools_by_name=tools_by_name,
            cancel_event=cancel_event,
            turn=turn,
        )

        # 3.1 emit tool_start / tool_end + 追加 ToolResultMessage
        for call, evs, result in results:
            for ev in evs:
                yield ev
            ctx.messages.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    content=result.content,
                    is_error=result.is_error,
                )
            )

        # 注: on_turn_end 已在 1.3 早检. 此处不再调, 避免重复.

        # 循环下一轮，LLM 拿到 tool_result 继续思考
    else:
        # for 正常跑完（未 break）→ max_turns
        stop_reason = "max_turns"

    # ---- 收尾轮（final answer turn）-----------------------------------------
    # 结构性强停（max_turns / hook_stop）时，如果这一整轮 run 从没产出过用户可见的
    # final 文本，就再给 LLM 一次**不带工具**的机会把话说完。不补的话前端只有一串
    # 工具 + 永久"思考中"、DB assistant_reply 为 NULL（见 WRAPUP_INSTRUCTION 事故）。
    #
    # 刻意不做：
    #   - 不改 stop_reason / stop_reason_detail（前端契约 + cap-005/cap-005b 的断言）
    #   - 不调 on_turn_start / on_turn_end（loop 已结束，调 guard 会改写 stop_reason_detail）
    #   - 不对 cancelled（用户主动中止）/ error（已吐过可见的失败文案）收尾
    if (
        final_answer_turn
        and stop_reason in ("max_turns", "hook_stop")
        and not saw_final_text
        and not cancel_event.is_set()
    ):
        state.turn_index += 1
        wrap_turn = state.turn_index
        # 指令进 ctx.messages：LLM 视野与本地记录一致（不做"只发 LLM 不留痕"的暗路）
        ctx.messages.append(UserMessage(content=WRAPUP_INSTRUCTION))
        yield TurnStartEvent(turn_index=wrap_turn)
        yield MessageStartEvent(turn_index=wrap_turn)

        wrap_out: dict = {}
        async for ev in _call_llm_once(driver, state, wrap_turn, [], wrap_out):
            yield ev

        wrap_msg: AssistantMessage | None = wrap_out["msg"]
        if wrap_msg is not None and (wrap_msg.text or "").strip():
            # 只认文本：tool_calls 一律丢 —— 工具已关，执行不了；留着还会让
            # last_assistant_text 跳过这条消息，等于没收尾
            ctx.messages.append(AssistantMessage(text=wrap_msg.text, usage=wrap_msg.usage))
        else:
            yield MessageUpdateEvent(turn_index=wrap_turn, kind="text_delta", delta=WRAPUP_FALLBACK_TEXT)
            ctx.messages.append(AssistantMessage(text=WRAPUP_FALLBACK_TEXT))

    yield AgentEndEvent(
        turn_index=state.turn_index,
        reason=stop_reason,
        total_input_tokens=state.total_input_tokens,
        total_output_tokens=state.total_output_tokens,
        stop_reason_detail=state.stop_reason_detail,
    )


async def _call_llm_once(
    driver: LLMDriver,
    state: AgentState,
    turn: int,
    tools: list[AgentTool],
    out: dict,
) -> AsyncGenerator[AgentEvent, None]:
    """调一次 LLM，结果写进 ``out``（async generator 没有返回值，只能这么传）.

    out["msg"]   : AssistantMessage | None（None = 流意外中断）
    out["error"] : str | None（调用抛异常时的文本）
    yields       : MessageUpdateEvent(text_delta)

    主循环轮与收尾轮共用（收尾轮传 tools=[]），避免那段 "stream_chat / 只有 .chat 的
    mock" 兼容分支被抄两遍抄漏。usage 累计也在这里做，两处调用方共享。

    优先走 stream_chat（真流式）；driver 没实现时降级 chat（一次拿）——
    这让老测试 mock（只有 .chat）不用改也能过。
    """
    out["msg"] = None
    out["error"] = None
    ctx = state.ctx
    try:
        state.is_streaming = True
        if hasattr(driver, "stream_chat"):
            async for kind, payload in driver.stream_chat(
                system_prompt=ctx.system_prompt,
                messages=ctx.messages,
                tools=tools,
            ):
                if kind == "text_delta":
                    yield MessageUpdateEvent(turn_index=turn, kind="text_delta", delta=payload)
                elif kind == "assistant_message":
                    out["msg"] = payload
        else:
            msg = await driver.chat(
                system_prompt=ctx.system_prompt,
                messages=ctx.messages,
                tools=tools,
            )
            out["msg"] = msg
            if msg is not None and msg.text:
                yield MessageUpdateEvent(turn_index=turn, kind="text_delta", delta=msg.text)
    except Exception as e:
        out["error"] = str(e)
    finally:
        state.is_streaming = False

    msg = out["msg"]
    if msg is not None:
        usage = msg.usage or {}
        state.total_input_tokens += usage.get("prompt_tokens", 0) or 0
        state.total_output_tokens += usage.get("completion_tokens", 0) or 0


async def _run_hook(hook: TurnHook, state: AgentState) -> bool:
    """执行 turn hook；兼容 sync / async 返回值."""
    result = hook(state)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


async def _execute_tools_parallel(
    calls: list[ToolCall],
    tools_by_name: dict[str, AgentTool],
    cancel_event: asyncio.Event,
    turn: int,
) -> list[tuple[ToolCall, list[AgentEvent], AgentToolResult]]:
    """并行跑一批 tool_calls，返回 [(call, [tool_start, tool_end], result)] 顺序保持.

    每个工具用独立 task 跑；单个工具失败不影响其它。
    """
    async def _run_one(call: ToolCall) -> tuple[ToolCall, list[AgentEvent], AgentToolResult]:
        events: list[AgentEvent] = [
            ToolStartEvent(
                turn_index=turn,
                tool_call_id=call.id,
                tool_name=call.name,
                args=call.args,
            )
        ]
        tool = tools_by_name.get(call.name)
        started = time.perf_counter()
        if tool is None:
            result = AgentToolResult.text(
                f"未知工具 '{call.name}'。可用工具：{list(tools_by_name.keys())}",
                is_error=True,
            )
        else:
            try:
                # update_callback：留待 P2 接入 ToolUpdateEvent 桥；P1 先给一个 no-op
                async def _noop_update(_delta: str) -> None:
                    return None

                result = await tool.execute(
                    tool_call_id=call.id,
                    args=call.args,
                    cancel_event=cancel_event,
                    update_callback=_noop_update,
                )
            except Exception as e:
                result = AgentToolResult.text(
                    f"[tool_exception] {type(e).__name__}: {e}",
                    is_error=True,
                )

        duration_ms = int((time.perf_counter() - started) * 1000)
        # 把 content 拍平成一段字符串放进 ToolEndEvent.output（观测用；LLM 视野走 ToolResultMessage）
        output_str = "\n".join(
            (c.text if isinstance(c, TextContent) else f"[image {getattr(c, 'mime_type', '')}]")
            for c in result.content
        )
        events.append(
            ToolEndEvent(
                turn_index=turn,
                tool_call_id=call.id,
                tool_name=call.name,
                output=output_str,
                is_error=result.is_error,
                duration_ms=duration_ms,
            )
        )
        return call, events, result

    tasks = [_run_one(c) for c in calls]
    return list(await asyncio.gather(*tasks))


def make_user_message(text: str) -> UserMessage:
    """helper：workflow 层把用户消息塞进 messages 的语法糖."""
    return UserMessage(content=text)


__all__ = [
    "run_agent_loop",
    "make_user_message",
    "TurnHook",
    "WRAPUP_INSTRUCTION",
    "WRAPUP_FALLBACK_TEXT",
]
