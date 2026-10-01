# test/test_agent_reasoning.py
"""思考过程（reasoning）透传链路：driver → loop → SSE bridge。

背景：deepseek-v4-flash 等推理模型的流式响应带 ``delta.reasoning_content``，
驱动层原先直接丢弃。现要求实时展示（不落库），且顺序为：
    思考（reasoning）→ 工具调用（tool_call）→ 最终回答（token/正文）
本文件按链路三段分别覆盖：驱动层独立通道、loop 事件映射、bridge SSE 帧。
"""
import json

from invoicing.agent.core.context import AgentContext, AgentState, AssistantMessage
from invoicing.agent.core.driver import OpenAIDriver
from invoicing.agent.core.events import AgentEndEvent, MessageUpdateEvent
from invoicing.agent.core.loop import run_agent_loop
from invoicing.agent.core.sse_bridge import bridge_events_to_sse
from invoicing.agent.sse import AgentSSEEmitter


# ============ driver：reasoning_content → ("reasoning_delta", ...) ============


class _Msg:
    """模拟 openai SDK 的 delta；带/不带 reasoning_content 两种形态。"""

    def __init__(self, content=None, reasoning=None):
        self.content = content
        if reasoning is not None:
            self.reasoning_content = reasoning


class _Choice:
    def __init__(self, msg):
        self.delta = msg


class _Chunk:
    def __init__(self, content=None, reasoning=None, usage=None):
        self.choices = [_Choice(_Msg(content, reasoning))]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kw):
        async def _gen():
            for c in self._chunks:
                yield c

        return _gen()


class _FakeClient:
    def __init__(self, chunks):
        self.chat = type("C", (), {"completions": _FakeCompletions(chunks)})()


async def _collect(driver):
    out = []
    async for item in driver.stream_chat(system_prompt="sys", messages=[], tools=[]):
        out.append(item)
    return out


async def test_driver_emits_reasoning_before_text_and_keeps_it_out_of_body():
    """reasoning 走独立通道：先于正文产出，且不混入最终 message.text。"""
    chunks = [
        _Chunk(reasoning="先看发票列表"),
        _Chunk(reasoning="，再统计。"),
        _Chunk(content="共 3 张"),
    ]
    driver = OpenAIDriver(_FakeClient(chunks), model="m")
    out = await _collect(driver)

    kinds = [k for k, _ in out]
    assert kinds == ["reasoning_delta", "reasoning_delta", "text_delta", "assistant_message"]
    assert [p for k, p in out if k == "reasoning_delta"] == ["先看发票列表", "，再统计。"]
    msg = out[-1][1]
    assert msg.text == "共 3 张"  # reasoning 不进 buffer / 正文
    assert msg.tool_calls == []


async def test_reasoning_does_not_affect_tool_call_detection():
    """前导 reasoning 后跟 tool_call JSON：仍判工具调用，且 JSON 不泄漏给前端。"""
    payload = '{"tool_call": [{"name": "invoice_list", "args": {"page": 1}}]}'
    chunks = [_Chunk(reasoning="我需要查一下发票。"), _Chunk(content=payload)]
    driver = OpenAIDriver(_FakeClient(chunks), model="m")
    out = await _collect(driver)

    assert [p for k, p in out if k == "reasoning_delta"] == ["我需要查一下发票。"]
    assert [p for k, p in out if k == "text_delta"] == []
    msg = out[-1][1]
    assert msg.tool_calls[0].name == "invoice_list" and msg.tool_calls[0].args == {"page": 1}


async def test_delta_without_reasoning_attr_still_works():
    """老 fake / 非推理模型 delta 无 reasoning_content 属性 → 不受影响。"""
    chunks = [_Chunk(content="普通回答")]
    driver = OpenAIDriver(_FakeClient(chunks), model="m")
    out = await _collect(driver)
    assert [k for k, _ in out] == ["text_delta", "assistant_message"]


# ============ loop：reasoning_delta → MessageUpdateEvent(thinking_delta) ============


class _ReasoningDriver:
    async def stream_chat(self, system_prompt, messages, tools):
        yield ("reasoning_delta", "让我想想")
        yield ("text_delta", "答案是 42")
        yield ("assistant_message", AssistantMessage(text="答案是 42"))


async def _run_loop(driver):
    ctx = AgentContext(tools=[], max_turns=2)
    state = AgentState(ctx=ctx)
    events = []
    async for ev in run_agent_loop(driver, state, session_id="t-reasoning"):
        events.append(ev)
    return events


async def test_loop_maps_reasoning_delta_to_thinking_event():
    events = await _run_loop(_ReasoningDriver())
    thinking = [
        e for e in events
        if isinstance(e, MessageUpdateEvent) and e.kind == "thinking_delta"
    ]
    assert [e.delta for e in thinking] == ["让我想想"]
    text = [
        e for e in events
        if isinstance(e, MessageUpdateEvent) and e.kind == "text_delta"
    ]
    assert [e.delta for e in text] == ["答案是 42"]


# ============ bridge：thinking_delta → emit("reasoning") 帧 ============


async def _frames(em: AgentSSEEmitter) -> list[dict]:
    em.close()
    out = []
    async for f in em.stream():
        out.append(json.loads(f[len("data: "):]))
    return out


async def test_bridge_emits_reasoning_frame():
    em = AgentSSEEmitter()
    stats: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="先想")
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="再答")
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    await bridge_events_to_sse(_events(), em, stats)
    frames = await _frames(em)
    assert frames[0] == {"type": "reasoning", "data": {"text": "先想"}}
    assert frames[1] == {"type": "token", "data": {"text": "再答"}}
    assert len(frames) == 2


async def test_bridge_skips_empty_thinking_delta():
    em = AgentSSEEmitter()
    stats: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="")
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    await bridge_events_to_sse(_events(), em, stats)
    assert await _frames(em) == []


# ============ 端到端：driver → loop → bridge 的帧顺序 ============


async def test_end_to_end_reasoning_precedes_answer():
    """假流块含 reasoning + content → reasoning 帧必须排在 token 帧之前。"""
    chunks = [_Chunk(reasoning="思考中"), _Chunk(content="最终答案")]
    driver = OpenAIDriver(_FakeClient(chunks), model="m")
    ctx = AgentContext(tools=[], max_turns=2)
    state = AgentState(ctx=ctx)
    em = AgentSSEEmitter()
    stats: list[dict] = []

    await bridge_events_to_sse(
        run_agent_loop(driver, state, session_id="t-e2e"), em, stats
    )
    frames = await _frames(em)
    assert [f["type"] for f in frames] == ["reasoning", "token"]
    assert frames[0]["data"]["text"] == "思考中"
    assert frames[1]["data"]["text"] == "最终答案"
