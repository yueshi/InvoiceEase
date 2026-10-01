# test/test_agent_tools_bridge.py
"""嵌入式工具桥：真实走 mcp.call_tool + 身份注入 + scope 拒绝。"""
import asyncio

from invoicing.agent.tools_bridge import build_tools_for_user
from invoicing.models import User


async def _noop(_delta: str) -> None:
    return None


async def test_build_and_call_list_invoices(db):
    user = User(username="bridge_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()

    tools = await build_tools_for_user(user)
    names = [t.name for t in tools]
    assert "invoice_list" in names and "invoice_delete" in names  # 全集都在 manifest

    t = next(t for t in tools if t.name == "invoice_list")
    res = await t.execute("tc-1", {"page": 1, "page_size": 5}, asyncio.Event(), _noop)
    assert res.is_error is False
    assert "total" in res.content[0].text or "items" in res.content[0].text  # 空库也是合法 JSON


async def test_scope_denied_for_employee(db):
    """employee 调 invoice_delete（invoice:admin）→ is_error + 权限字样。"""
    user = User(username="bridge_u2", password_hash="x", role="employee")
    db.add(user)
    db.commit()

    tools = await build_tools_for_user(user)
    t = next(t for t in tools if t.name == "invoice_delete")
    res = await t.execute("tc-2", {"invoice_id": 999}, asyncio.Event(), _noop)
    assert res.is_error is True
    assert "权限" in res.content[0].text


async def test_unknown_args_wrapped_as_error(db):
    """参数不合法（缺 invoice_id）→ SDK 校验异常 → is_error 结果，不炸 loop。"""
    user = User(username="bridge_u3", password_hash="x", role="admin")
    db.add(user)
    db.commit()

    tools = await build_tools_for_user(user)
    t = next(t for t in tools if t.name == "invoice_detail")
    res = await t.execute("tc-3", {}, asyncio.Event(), _noop)
    assert res.is_error is True
