"""proposal_registry 机制测试（P0-2 软白名单）。

覆盖范围：注册机制本身（装饰器 / get_proposal / 未注册拒绝）。
20 个工具的完整性断言在 test_proposal_registry_completeness.py（Task 10）。
"""
import pytest

from invoicing.mcp.proposal_registry import (
    PROPOSAL_REGISTRY,
    get_proposal,
    register_proposal,
)


def test_register_proposal_registers_under_tool_name():
    """装饰器把函数登记到 PROPOSAL_REGISTRY[tool_name]。"""
    name = "__test_register__"

    @register_proposal(name)
    def _fn(db, **kw):
        return {"ok": True}

    try:
        assert PROPOSAL_REGISTRY[name] is _fn
        assert get_proposal(name) is _fn
    finally:
        PROPOSAL_REGISTRY.pop(name, None)


def test_get_proposal_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        get_proposal("__never_registered_tool__")


def test_register_proposal_returns_original_function():
    """装饰器不包装函数（返回原对象），便于调用方直接复用。"""

    @register_proposal("__test_identity__")
    def _fn(db, **kw):
        return {"n": 1}

    try:
        assert _fn.__name__ == "_fn"
        assert _fn(None) == {"n": 1}  # 原函数行为不变
    finally:
        PROPOSAL_REGISTRY.pop("__test_identity__", None)