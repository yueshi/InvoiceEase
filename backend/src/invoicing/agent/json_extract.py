"""LLM 输出的 JSON 容错提取（全项目共享）.

历史：这份逻辑曾有三份拷贝——
    recon/react.py._extract_json（直接 loads → ```json block → 平衡括号）
    agent/core/driver.py._extract_json_object（剥 fence → 首 { 到尾 } 贪婪切片）
    agent/intents.py._extract_json_object（driver 版的逐字节拷贝）
统一为 extract_json_dict，两个消费层（agent.core.driver / recon.react / agent.intents）
都向下依赖 auditmind.core。

合并语义（相对旧实现的两处行为变化，均严格更宽容或终态等价）：
1. driver 旧版取"首 { 到尾 }"贪婪切片（'{"a":1} x {"b":2}' → loads 失败 → 调用方
   text 兜底）；平衡扫描取首个平衡候选（→ {"a":1}；结构不符时调用方同样 text 兜底）。
2. react 旧版首个平衡候选 loads 失败立即返 None；本实现继续扫描后续候选，
   对 '```json\n{bad}\n```\n{"ok":1}' 这类输出能救回第二个候选。
"""
from __future__ import annotations

import json
import re

_JSON_BLOCK_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_dict(text: str) -> dict | None:
    """从 LLM 输出里提取一个 JSON dict，找不到返回 None.

    容错顺序：
    1. 直接 json.loads（结果是 dict 才收——[1,2] / "str" / 123 一律不要）
    2. ```json ... ``` fenced code block 正则（非贪婪，DOTALL）
    3. 平衡括号扫描：找**最外层**平衡的 ``{...}`` 候选。

    2026-09-22 修复：原实现从每个 ``{`` 开始找平衡，遇到 json.loads 成功就
    返回（"第一个能 parse 的胜出"）。问题在于流式场景中**外层 JSON 还没闭
    合**，但内层 ``{"name":..., "args":{}}`` 这种工具 spec 已经能完整
    parse，函数就 return 了内层——而内层不是 P3 tool_call wrapper，
    LLM 流式吐出 `{"tool_call": [{"name": "...", "args": {}},`
    这种"外层未闭合 + 内层已闭合"中间态时会被误识别。

    修正：从最外层 ``{`` 开始扫描，找到**长度最大**的平衡 dict 优先返回。
    外层完整可用时永远胜出；只有外层整体 parse 失败才退回候选。
    """
    text = (text or "").strip()
    if not text:
        return None

    # 1. 直接
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2. fenced code block
    m = _JSON_BLOCK_RX.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 3. 平衡括号扫描——优先最外层完整候选
    #    收集所有"以当前 '{' 起、depth 归 0 处止"的合法 JSON dict，
    #    按 span 长度倒序（外层一定比内层 span 长），首选 span 最大者。
    candidates: list[tuple[int, int, dict]] = []  # (start, length, parsed_obj)
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            candidates.append((start, i - start + 1, obj))
                    except json.JSONDecodeError:
                        pass
                    break  # 当前起点的 balance 已定位（不论 parse 成败），换下个 '{'
        start = text.find("{", start + 1)

    if not candidates:
        return None
    # 取 span 最长（最外层完整 JSON）
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][2]


__all__ = ["extract_json_dict"]
