"""LLM Driver：Agent Loop 与 LLM 之间的适配层.

设计（P3 起）：
- Driver.chat(...)         非流式（保留给 unit test / 非实时场景）
- Driver.stream_chat(...)  流式，async generator 出 ("text_delta", str) / ("assistant_message", AssistantMessage)

用 prompt-engineered tool 调用（不用 provider native `tool_calls` streaming），
原因见 design/pi-mono-python-integration-plan-2026-07-22.md：
    Qwen3.6-27B + do.top 代理 native tool_use 支持不完整；
    intents.py 已经踩过 native JSON mode 坑，同一套兼容参数复用最稳。

工具调用协议（P3 简化）：
    - 要调工具 → 输出 JSON `{"tool_call": [{"name":"...", "args":{...}}, ...]}`
      （可以裹在 ```json ... ``` 里）
    - 要给最终答案 → **直接输出纯文本**（可以 Markdown）

判别规则（2026-07-30 加固）：
    早期只看首个非空白字符（`{`/`` ``` `` → tool_call，否则 final）。但真实模型
    （Qwen 类）常在 tool_call JSON 前垫一句自然语言（"首先，搜索 CWE-89 的知识库…"），
    首字符判别会把整段（含 JSON）误当 final 发给前端 → 工具从不执行、多轮中断。
    现改为：
    1. 首字符 `{`/裸 fence/```json → tool_call；`chart-*`/其它 lang fence → final（不变）
    2. 前导是自然语言但正文里出现可解析的 `{"tool_call":...}`/`{"action":...}` 结构
       → 判 tool_call（靠 extract_json_dict 抽平衡 JSON）
    3. 纯自然语言（无可解析 tool_call 结构）→ final
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from typing import Any, AsyncGenerator, Protocol, Union

from invoicing.agent.core.context import AssistantMessage, Message, ToolCall
from invoicing.agent.json_extract import extract_json_dict


# ============ 工具调用协议规约 prompt ============


TOOL_CALL_INSTRUCTIONS = """
## 工具调用协议

你可以调用下列工具收集信息，或直接给出最终答案。

**要调用工具时**：只输出 JSON 对象，不要任何前后自然语言：
```json
{"tool_call": [{"name": "工具名", "args": {"key": "value"}}]}
```
- tool_call 数组可以包含多个调用，一次并行执行
- 工具执行后你会看到 `[tool_result] 输出...` 的用户消息
- 拿到结果后继续下一轮决策
- ⚠️ 严禁使用任何标签/XML 风格的调用写法（无论什么分隔符或属性形式）——本平台只识别上面的 JSON 协议，其它写法不会被引擎执行。

**要给出最终答案时**：直接输出你的回答（中文，支持 Markdown），不要包 JSON、不要写 `{...}`。开头不要以 `{` 或 ` ``` ` 起手，否则会被误当成工具调用。
"""


def build_tools_manifest(tools: list) -> str:
    """把工具目录序列化成一段 markdown，塞进 system prompt."""
    if not tools:
        return ""
    lines = ["\n## 可用工具\n"]
    for t in tools:
        lines.append(f"### `{t.name}`")
        lines.append(f"{t.description}")
        try:
            schema_str = json.dumps(t.schema, ensure_ascii=False, indent=2)
        except Exception:
            schema_str = "{}"
        lines.append(f"\n参数 schema:\n```json\n{schema_str}\n```\n")
    return "\n".join(lines)


# ============ Driver 协议 ============


StreamChunk = Union[
    tuple[str, str],  # ("text_delta", chunk)  — final path 流式增量
    tuple[str, AssistantMessage],  # ("assistant_message", msg)  — 流末最终消息
]


class LLMDriver(Protocol):
    """Loop 依赖的 LLM 客户端最小接口."""

    async def chat(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list,
    ) -> AssistantMessage:
        ...

    def stream_chat(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list,
    ) -> AsyncGenerator[StreamChunk, None]:
        ...


# ============ LiteLLM 实现 ============


# 探测多少字符后就能判定这是 tool_call 还是 final text（首字符即结构起始时用）
_PROBE_CHARS = 16

# 前导是纯自然语言（缓冲里还没出现 `{`/```` ``` ````）时，攒够这么多字符仍无结构
# → 判 final。
#
# 2026-09-22 调整：32 → 256。
# 历史：阈值原取 32 是为照顾「短引导句 + 后面跟 JSON tool_call」（如
# "首先，搜索 CWE-89 的知识库。\n```json{...}```" 约 18 字）。但 main_agent 的
# system prompt 让 LLM 写 Stage 1 自评（典型 80-200 字中文）+ tool_call JSON，
# 自评长度经常超 32，导致 probe 在第 32 字就锁死成 final → JSON 后面到了
# 只被当文本追加、循环结束 tool_calls=[]，工具从不执行（DB 里多条
# agent_conversations 实证：tool_calls=0、assistant_reply 是 Stage 1 + JSON 原文）。
# 256 留够空间让 Stage 1 自评完整走完，JSON 一闭合 extract_json_dict 就能识别。
# 代价：纯 final 长答案前 256 字不能 flush 给前端（一次冲刷可见）。可接受：
#   - 大多数 final 答案在 256 字内，要么首字符是 `{`/fence 走 fast path
#   - 即使打 256 字一次冲，用户的"打字机"体感差异 < 1 秒
_NL_FINAL_CHARS = 256

# 前导自然语言 + tool_call 场景下，缓冲区最多攒多少字符还没解析出 tool_call
# 结构就放弃、当 final（防止 JSON 永远不闭合时流式挂死）。
_STREAM_HARD_CAP = 8192

# 空响应兜底文案：模型/网关返回 0 字符 content 时的可见占位。
# 2026-09-22 实测（aiping 那套网关后的 qwen3.8）：finish_reason=stop、
# completion_tokens=158，但 content 一个字符都没有——生成被吞。没有兜底的话
# 前端是空白气泡，且 run_agent 会把上一轮答复误记成本轮结果（见 main_agent 侧切片修复）。
_EMPTY_REPLY_TEXT = "（LLM 无输出）"


# ============ 模型原生工具调用方言（兜底） ============
# 背景（用户会话落库实证）：DeepSeek 类模型偶发把「原生工具调用标记」当纯文本输出
# （全角竖线包裹的标签块：calls 包裹 invoke，invoke 带 name，parameter 子块带 name/string
# 属性）。驱动层只认 prompt-JSON 协议时会把它当 final text 直通前端、工具从不执行。
# 这里做整段兜底解析 + 流式前缀探测；关键词分段拼接书写，避免整段标记出现在
# 源码/日志/工具输出中被二次解析。
_DSML_BAR = "｜｜DSML｜｜"
_DSML_OPEN = "<" + _DSML_BAR
_INVOKE_KW = "in" + "voke"        # 分段书写：防整段关键词外泄
_PARAM_KW = "para" + "meter"
_NAME_ATTR = "na" + "me="
_DSML_INVOKE_RE = re.compile(
    "<" + re.escape(_DSML_BAR) + r"\s*" + _INVOKE_KW + r"\s+" + _NAME_ATTR + r'"([^"]*)"\s*>(.*?)'
    + "</" + re.escape(_DSML_BAR) + r"\s*" + _INVOKE_KW + r"\s*>",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    "<" + re.escape(_DSML_BAR) + r"\s*" + _PARAM_KW + r"\s+" + _NAME_ATTR + r'"([^"]*)"[^>]*>(.*?)'
    + "</" + re.escape(_DSML_BAR) + r"\s*" + _PARAM_KW + r"\s*>",
    re.DOTALL,
)
# 方言残缺（有分隔串但解析不出 invoke）时给用户的可见提示（绝不透出原始标记）
_DSML_FALLBACK_TEXT = "（本轮模型输出格式异常，已忽略。请重试或换个问法。）"


def _parse_native_tool_calls(content: str) -> list[ToolCall] | None:
    """兜底解析模型原生工具调用方言 → ToolCall 列表；无标记或解析不出 invoke 返回 None。

    参数组装：优先名为 "args" 的参数块（值须为 JSON object）；否则收集除 "name" 外的
    散装参数（值尝试 JSON 解码，失败保留原始字符串）。
    """
    if not content or _DSML_BAR not in content:
        return None
    calls: list[ToolCall] = []
    for inv in _DSML_INVOKE_RE.finditer(content):
        name = (inv.group(1) or "").strip()
        body = inv.group(2) or ""
        params: dict[str, str] = {}
        for pm in _DSML_PARAM_RE.finditer(body):
            params[(pm.group(1) or "").strip()] = (pm.group(2) or "").strip()
        if not name:
            name = params.get("name", "").strip()
        if not name:
            continue
        args: dict[str, Any] = {}
        if "args" in params:
            try:
                parsed = json.loads(params["args"])
                if isinstance(parsed, dict):
                    args = parsed
            except (json.JSONDecodeError, ValueError):
                args = {}
        if not args:
            for k, v in params.items():
                if k in ("name", "args"):
                    continue
                try:
                    args[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    args[k] = v
        calls.append(ToolCall(id=f"tc-{uuid.uuid4().hex[:8]}", name=name, args=args))
    return calls if calls else None


def _dict_is_tool_call(data: dict | None) -> bool:
    """判断 extract_json_dict 抽出来的 dict 是不是工具调用结构.

    认 P3 协议 `{"tool_call": [...]}` 与老协议 `{"action": "tool_call"|"final"}`；
    其它 dict（如 chart-* 的图表数据 `{"title":..., "data":[...]}`）不算。
    """
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("tool_call"), list):
        return True
    if data.get("action") in ("tool_call", "final"):
        return True
    return False


def _probe_stream_buffer(stripped: str) -> bool | None:
    """流式探测：返回 True=tool_call，False=final，None=还差字符判不了.

    在 _looks_like_tool_call_prefix（只看首字符）基础上加一层容错：
    前导是自然语言、但正文里已经出现可解析的 tool_call 结构时，也判 tool_call。

    规则顺序：
    1. 首字符即 `{` / 裸 fence / ```json → 交给 _looks_like_tool_call_prefix（含 chart-* → final）
    2. 首字符是普通文本（前导自然语言）：
       - 缓冲里还没出现 `{` 或 ```` ``` ```` → 暂判不了，等更多字符；
         但攒够 _NL_FINAL_CHARS 仍是纯文本 → final（保住打字机）
       - 出现了 `{`/```` ``` ````：尝试 extract_json_dict
         - 抽到 tool_call 结构 → tool_call
         - 抽到非 tool_call dict（如 chart 数据 OR 流式中间态的内层工具 spec）
           且**外层 `{` 净开闭深度已归零** → final
         - 抽到非 tool_call dict 但**外层 `{` 还没闭合**（流式中间态抓到了内层
           工具 spec `{"name": ..., "args": {}}` 但外层 `{"tool_call": [...]}` 还没闭合）
           → None 继续等外层 JSON 闭合 — 2026-09-22 加这条，解决流式双工具
           ``{"tool_call": [{"name":..., "args":{}},`` 这种中间态被判 final 的 bug
         - 还没抽到（JSON 未闭合）→ None 继续等，直到 _STREAM_HARD_CAP 兜底 final
    """
    if not stripped:
        return None

    # 情况 0：模型原生调用方言——开头即判 tool_call（不等 256 字符）；
    # 疑似前缀（半截分隔串）时继续等待更多字符
    if stripped.startswith(_DSML_OPEN):
        return True
    if _DSML_OPEN.startswith(stripped):
        return None

    # 情况 1：首字符即结构起始 → 沿用保守首字符规则（chart-* 已在其中判 final）
    if stripped[0] == "{" or stripped.startswith("```"):
        return _looks_like_tool_call_prefix(stripped)

    # 情况 2：前导自然语言
    has_brace = "{" in stripped
    has_fence = "```" in stripped
    if not has_brace and not has_fence:
        # 纯文本，攒够阈值就判 final（打字机）；否则再等等
        return False if len(stripped) >= _NL_FINAL_CHARS else None

    # 正文里出现了 JSON/fence，试着抽 tool_call 结构
    data = extract_json_dict(stripped)
    if data is not None:
        if _dict_is_tool_call(data):
            return True
        # 非 tool_call 的 dict：要么是 chart-* 数据，要么是流式中间态抓到的内层
        # 工具 spec（最外层 ``{"tool_call":[...]}`` 还没闭合）。
        # 用「buffer 里 `{` 与 `}` 净开闭深度」判定外层是否已闭合。
        depth = 0
        for ch in stripped:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth > 0:
            # 外层还没闭合，等后续 chunk（不可此时锁 final，否则后续闭合也救不回）
            return None
        return False

    # 出现了 { 但还没法 parse → 等；超过硬上限放弃当 final
    return None if len(stripped) < _STREAM_HARD_CAP else False


def _looks_like_tool_call_prefix(stripped: str) -> bool | None:
    """探测阶段判定：返回 True=tool_call，False=final，None=还差字符判不了.

    规则（保守）：
    - 首字符 '{' → tool_call（原生 JSON 起始）
    - 以 '```' 起头：需要看后面的 language tag
        - 无 tag 或 tag 是 'json' → tool_call（协议规定的 fenced JSON）
        - 有其它 tag（如 chart-pie / python / markdown）→ final（是 markdown 代码块）
        - tag 还没出（只看到 '```'）→ None 等更多字符
    - 其它字符起头 → final
    """
    if not stripped:
        return None
    if stripped[0] == "{":
        return True
    if stripped.startswith("```"):
        rest = stripped[3:]
        # 找语言标记的边界：换行 / 空格 / '{'
        # 头一行为空（``` 后紧跟换行）→ 裸 fence，认作 tool_call
        for i, ch in enumerate(rest):
            if ch == "\n" or ch == " " or ch == "{":
                lang = rest[:i].strip().lower()
                if lang == "" or lang == "json":
                    return True
                return False  # chart-pie / python / … 都当 markdown
        # 没找到边界：tag 名还在流里
        return None
    return False


class OpenAIDriver:
    """基于 openai AsyncOpenAI 的 driver（流式 + 非流式）.

    工具调用协议与流式探测沿用 AuditMind 版（_probe_stream_buffer 等），
    只把底层 chat_client 换成 openai SDK 原生异步客户端。
    """

    def __init__(self, client, model: str, temperature: float = 0.3, max_tokens: int = 2000):
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list,
    ) -> AssistantMessage:
        """非流式一次拿全 content——收尾轮 / unit test / 非实时场景。"""
        oa_messages = _build_openai_messages(system_prompt, messages, tools)
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=oa_messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            print(f"[agent-driver] LLM 调用失败: {type(e).__name__}: {e}", file=sys.stderr)
            raise

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            print("[agent-driver] LLM 返回空 content", file=sys.stderr)
            return AssistantMessage(text=_EMPTY_REPLY_TEXT)

        usage = getattr(resp, "usage", None)
        msg = _parse_assistant_reply(content)
        msg.usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }
        return msg

    async def stream_chat(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list,
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式——边收边判别 tool_call vs final（判别逻辑同 AuditMind）。

        判别后：
          - tool_call 路径：收完一次性解析（全程不 yield，防前导话术泄漏给前端）
          - final 路径：先把缓冲整块 yield，之后逐 chunk yield
        流末 yield ("assistant_message", AssistantMessage)（含 usage）。
        """
        oa_messages = _build_openai_messages(system_prompt, messages, tools)

        buffer = ""
        mode: str | None = None  # None=探测中 | "tool_call" | "final"
        full_content = ""
        usage: dict = {}

        try:
            # ponytail: stream_options.include_usage 依赖 provider 支持；
            # 不支持的端点（如个别 Ollama 版）可去掉此参数，usage 记为 0，不影响主流程
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=oa_messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                    }
                choices = getattr(chunk, "choices", None) or []
                delta = ""
                if choices and choices[0].delta is not None:
                    delta = choices[0].delta.content or ""
                if not delta:
                    continue
                full_content += delta

                if mode is None:
                    buffer += delta
                    verdict = _probe_stream_buffer(buffer.lstrip())
                    if verdict is True:
                        mode = "tool_call"
                    elif verdict is False:
                        mode = "final"
                        yield ("text_delta", buffer)
                        buffer = ""
                    # None → 继续攒 buffer
                elif mode == "final":
                    yield ("text_delta", delta)
                # mode == "tool_call" 时不 yield，等收完解析
        except Exception as e:
            print(f"[agent-driver] LLM 流式调用失败: {type(e).__name__}: {e}", file=sys.stderr)
            raise

        # 流已结束但还没判定（超短回复）
        if mode is None:
            if _probe_stream_buffer(buffer.lstrip()) is True:
                mode = "tool_call"
            else:
                mode = "final"
                if buffer:
                    yield ("text_delta", buffer)

        if mode == "tool_call":
            msg = _parse_assistant_reply(full_content)
            if not msg.tool_calls and full_content.strip() and _DSML_BAR in full_content:
                # 原生方言残缺（有分隔串但解析不出 invoke）：绝不透出原始标记
                yield ("text_delta", _DSML_FALLBACK_TEXT)
                msg = AssistantMessage(text=_DSML_FALLBACK_TEXT)
            elif not msg.tool_calls and full_content.strip():
                # 以为是 tool_call 但解析失败 → 退化为 final，别让用户看不到输出
                print(
                    f"[agent-driver] tool_call 路径解析失败，退化为 final text（预览：{full_content[:200]!r}）",
                    file=sys.stderr,
                )
                yield ("text_delta", full_content)
                msg = AssistantMessage(text=full_content)
        else:
            msg = AssistantMessage(text=full_content)

        # 空响应兜底：整条流一个字符都没有（网关吞输出）
        if not full_content.strip() and not msg.tool_calls:
            print("[agent-driver] LLM 返回空 content（流式）", file=sys.stderr)
            yield ("text_delta", _EMPTY_REPLY_TEXT)
            msg = AssistantMessage(text=_EMPTY_REPLY_TEXT)

        msg.usage = usage
        yield ("assistant_message", msg)


# ============ 内部工具函数 ============


def _build_openai_messages(
    system_prompt: str,
    messages: list[Message],
    tools: list,
) -> list[dict[str, Any]]:
    """拼装 OpenAI chat/completions 风格 messages（system + 工具目录 + 历史）.

    2026-09-22: tools 为空时不再拼工具调用协议段 —— 收尾轮（loop 的 final answer turn）
    以 tools=[] 调用，留着"怎么发 tool_call JSON"的说明只会诱导模型继续发 JSON，
    而收尾轮的 JSON 会被丢掉、前端又拿不到文本，收尾目的落空。
    build_tools_manifest 对空目录本来就返 ""（见上），这里把协议段一起跳过，语义对齐。
    生产链路 tools 恒非空（main_agent 10 个 / legacy workflow 各 2 个），拼装不变。
    """
    full_system = system_prompt.rstrip()
    manifest = build_tools_manifest(tools)
    if manifest:
        full_system += "\n\n" + manifest + "\n\n" + TOOL_CALL_INSTRUCTIONS

    oa_messages: list[dict[str, Any]] = [{"role": "system", "content": full_system}]
    for m in messages:
        oa_messages.append(_message_to_openai(m))
    return oa_messages


def _message_to_openai(m: Message) -> dict[str, Any]:
    """AuditMind 内部 Message → OpenAI chat/completions messages 格式."""
    if m.role == "user":
        return {"role": "user", "content": m.content}
    if m.role == "assistant":
        # tool_calls 存在时，把 tool_call JSON 原样发回（帮助 LLM 保持自洽）
        text = m.text
        if m.tool_calls:
            payload = {
                "tool_call": [{"name": tc.name, "args": tc.args} for tc in m.tool_calls],
            }
            text = json.dumps(payload, ensure_ascii=False)
        return {"role": "assistant", "content": text or ""}
    if m.role == "tool_result":
        # 用 user 角色回填工具结果（Qwen3 等模型对 `role=tool` 支持不稳，走 user 最兼容）
        joined = "\n".join(
            (c.text if hasattr(c, "text") else f"[image {getattr(c, 'mime_type', '')}]")
            for c in m.content
        )
        prefix = "[tool_error]" if m.is_error else "[tool_result]"
        return {"role": "user", "content": f"{prefix} {joined}"}
    raise ValueError(f"unknown message role: {m.role}")


def _parse_assistant_reply(content: str) -> AssistantMessage:
    """把 LLM 返回的字符串解析成 AssistantMessage.

    P3 后协议：
    - tool_call：JSON `{"tool_call": [{"name":..., "args":...}]}`（可裹 markdown，
      也容忍前导自然语言，如 "首先，搜索…\\n```json{...}```"）
    - final：直接纯文本

    判定（2026-07-30 加固）：不再用首字符 gate，改为直接 extract_json_dict 抽平衡 JSON，
    再看抽出来的 dict 是不是 tool_call 结构（_dict_is_tool_call）：
    - 是 → tool_call（治前导自然语言把工具调用带偏成 final 的 bug）
    - 否（chart 数据 / 普通文本里的花括号 / 抽不到）→ 当纯文本 final
    也兼容老协议 `{"action":"tool_call","calls":[...]}` / `{"action":"final","text":"..."}`。
    """
    # 兜底：模型原生调用方言（DeepSeek 类偶发）→ 解析成真工具调用；残缺方言给可见提示
    native = _parse_native_tool_calls(content)
    if native is not None:
        return AssistantMessage(text="", tool_calls=native)
    if _DSML_BAR in content:
        return AssistantMessage(text=_DSML_FALLBACK_TEXT)

    data = extract_json_dict(content)
    if _dict_is_tool_call(data):
        # P3 协议
        if isinstance(data.get("tool_call"), list):
            return AssistantMessage(text="", tool_calls=_parse_calls_list(data["tool_call"]))

        # 兼容老协议
        action = data.get("action")
        if action == "tool_call":
            return AssistantMessage(text="", tool_calls=_parse_calls_list(data.get("calls") or []))
        if action == "final":
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                return AssistantMessage(text=text)

    # 非 tool_call 结构 / 抽不到 JSON → 当纯文本 final
    return AssistantMessage(text=content)


def _parse_calls_list(calls: list) -> list[ToolCall]:
    parsed: list[ToolCall] = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name or not isinstance(name, str):
            continue
        args = c.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        parsed.append(ToolCall(id=f"tc-{uuid.uuid4().hex[:8]}", name=name, args=args))
    return parsed


__all__ = [
    "LLMDriver",
    "OpenAIDriver",
    "TOOL_CALL_INSTRUCTIONS",
    "build_tools_manifest",
]
