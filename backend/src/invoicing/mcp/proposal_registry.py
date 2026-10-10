"""P0-2 软白名单：写工具的 execute 函数注册表（v1.1 §7.5）。

`@register_proposal("<tool_name>")` 把工具的实际落库函数登记入表；
`confirm_execute` 只允许消费注册过的 tool_name —— 在 P0-3 硬白名单落地前，
这是"哪些工具能被执行"的唯一清单来源。
"""
from typing import Callable

PROPOSAL_REGISTRY: dict[str, Callable] = {}


def register_proposal(tool_name: str):
    """装饰器：把 execute 函数登记到 PROPOSAL_REGISTRY[tool_name]。"""
    def deco(fn: Callable) -> Callable:
        PROPOSAL_REGISTRY[tool_name] = fn
        return fn
    return deco


def get_proposal(tool_name: str) -> Callable:
    if tool_name not in PROPOSAL_REGISTRY:
        raise KeyError(f"tool not registered for two-phase: {tool_name}")
    return PROPOSAL_REGISTRY[tool_name]