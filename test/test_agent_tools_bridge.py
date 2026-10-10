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
    # 2026-10-05：清单按权限过滤——员工有查票/交票能力，但没有 invoice:admin 工具
    # P0-2：写工具以 *_proposal 暴露
    assert "invoice_list" in names and "invoice_delete_proposal" not in names

    t = next(t for t in tools if t.name == "invoice_list")
    res = await t.execute("tc-1", {"page": 1, "page_size": 5}, asyncio.Event(), _noop)
    assert res.is_error is False
    assert "total" in res.content[0].text or "items" in res.content[0].text  # 空库也是合法 JSON


async def test_scope_denied_at_execution_layer(db):
    """纵深防御：即使绕过清单过滤手工构造适配器，执行层 scope 门仍然拒绝。"""
    from invoicing.agent.tools_bridge import McpToolAdapter
    from invoicing.mcp.server import mcp

    user = User(username="bridge_u2", password_hash="x", role="employee")
    db.add(user)
    db.commit()

    infos = await mcp.list_tools()
    info = next(i for i in infos if i.name == "invoice_delete_proposal")
    t = McpToolAdapter(info, user_id=user.id, username=user.username, role=user.role)
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
