# test/test_agent_loop.py
"""run_agent_loop：终止条件 / 工具执行 / 异常隔离 / WRAPUP 收尾轮。"""
import asyncio
from invoicing.agent.core.context import (
    AgentContext, AgentState, AssistantMessage, ToolCall,
)
from invoicing.agent.core.events import AgentEndEvent, ToolEndEvent
from invoicing.agent.core.loop import run_agent_loop
from invoicing.agent.core.tools import AgentToolResult


class FakeDriver:
    """按脚本逐轮吐 assistant_message（只实现 stream_chat，loop 优先走它）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.last_tools = None

    async def stream_chat(self, system_prompt, messages, tools):
        self.calls += 1
        self.last_tools = tools
        reply = self.replies.pop(0)
        if reply.text:
            yield ("text_delta", reply.text)
        yield ("assistant_message", reply)


class EchoTool:
    name = "echo"
    description = "回显"
    schema = {"type": "object"}

    async def execute(self, tool_call_id, args, cancel_event, update_callback):
        return AgentToolResult.text(f"echo:{args.get('x')}")


class BoomTool:
    name = "boom"
    description = "总是抛错"
    schema = {"type": "object"}

    async def execute(self, tool_call_id, args, cancel_event, update_callback):
        raise RuntimeError("炸了")


def _state(tools, max_turns=8):
    ctx = AgentContext(tools=tools, max_turns=max_turns)
    return AgentState(ctx=ctx)


async def _run(driver, state):
    events = []
    async for ev in run_agent_loop(driver, state, session_id="t1"):
        events.append(ev)
    return events


async def test_plain_final_stops():
    driver = FakeDriver([AssistantMessage(text="直接回答")])
    state = _state([])
    events = await _run(driver, state)
    assert isinstance(events[-1], AgentEndEvent)
    assert events[-1].reason == "no_more_tool_calls"
    assert driver.calls == 1


async def test_tool_call_executes_then_final():
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="echo", args={"x": 1})]),
        AssistantMessage(text="完成"),
    ])
    state = _state([EchoTool()])
    events = await _run(driver, state)
    tool_ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert len(tool_ends) == 1 and tool_ends[0].output == "echo:1" and not tool_ends[0].is_error
    assert events[-1].reason == "no_more_tool_calls"
    # 工具结果以 ToolResultMessage 入历史
    from invoicing.agent.core.context import ToolResultMessage
    assert any(isinstance(m, ToolResultMessage) and m.content[0].text == "echo:1"
               for m in state.ctx.messages)


async def test_unknown_tool_is_error_result():
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="nope", args={})]),
        AssistantMessage(text="好吧"),
    ])
    state = _state([EchoTool()])
    events = await _run(driver, state)
    tool_ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert tool_ends[0].is_error and "未知工具" in tool_ends[0].output


async def test_tool_exception_isolated():
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="boom", args={})]),
        AssistantMessage(text="炸了但我继续"),
    ])
    state = _state([BoomTool()])
    events = await _run(driver, state)
    tool_ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert tool_ends[0].is_error and "RuntimeError" in tool_ends[0].output
    assert events[-1].reason == "no_more_tool_calls"


async def test_max_turns_triggers_wrapup():
    """跑满轮数且从未产出可见 final → 补一轮 tools=[] 逼出结论。"""
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="echo", args={"x": 1})]),
        AssistantMessage(text="收尾结论"),
    ])
    state = _state([EchoTool()], max_turns=1)
    events = await _run(driver, state)
    assert driver.calls == 2
    assert driver.last_tools == []            # 收尾轮工具已关闭
    assert events[-1].reason == "max_turns"   # stop_reason 不被收尾轮改写


async def test_cancel_stops():
    driver = FakeDriver([AssistantMessage(text="x"), AssistantMessage(text="y")])
    state = _state([])
    cancel = asyncio.Event()
    cancel.set()
    events = []
    async for ev in run_agent_loop(driver, state, session_id="t", cancel_event=cancel):
        events.append(ev)
    assert events[-1].reason == "cancelled"
    assert driver.calls == 0
