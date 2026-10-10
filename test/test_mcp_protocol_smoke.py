"""协议层冒烟：经 MCP server 真实调用路径（非直调实现函数）验证参数绑定。

存在的理由（final review 启发）：此前所有 P0-2 测试都直调 `mcp.tools.*`，
server.py 包装层的**位置参数错位**完全不被覆盖 —— `bank_account_save_proposal`
漏了 `bank_code`，导致 remark/is_default/enabled/idempotency_key 整体后移一位，
`enabled=false` 停用不了账号、带幂等键时 confirm 崩溃。
本文件为这类错位提供回归网。
"""
import json

import pytest

from invoicing.db import SessionLocal
from invoicing.mcp.server import build_server
from invoicing.models.proposal import Proposal


@pytest.fixture()
def server():
    return build_server()


def _result_json(result) -> dict:
    """取工具返回的 JSON：优先 structured_content，回落到 content[0].text。"""
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


def _last_proposal_payload() -> dict:
    with SessionLocal() as db:
        p = db.query(Proposal).order_by(Proposal.created_at.desc(), Proposal.token.desc()).first()
        assert p is not None, "未产生提案"
        return dict(p.payload)


@pytest.mark.asyncio
async def test_bank_account_save_proposal_args_bind_by_name(db, mcp_admin_auth, server):
    """协议的 arguments → 提案 payload 必须逐字段同位（防包装层位置错位）。"""
    await server.call_tool("bank_account_save_proposal", {
        "account_no": "6222021234567890",
        "account_name": "本司基本户",
        "bank_name": "工商银行",
        "remark": "主账号",
        "is_default": True,
        "enabled": False,
    })
    payload = _last_proposal_payload()
    assert payload["account_no"] == "6222021234567890"
    assert payload["account_name"] == "本司基本户"
    assert payload["bank_name"] == "工商银行"
    assert payload["remark"] == "主账号"
    assert payload["is_default"] is True
    assert payload["enabled"] is False  # 错位时会变成 is_default/enabled 互换


@pytest.mark.asyncio
async def test_bank_account_save_proposal_idempotency_key_not_leaked(db, mcp_admin_auth, server):
    """幂等键必须落在 idempotency_key，不得被写进业务字段（如 remark/bank_code/enabled）。"""
    await server.call_tool("bank_account_save_proposal", {
        "account_no": "6222021234567891",
        "idempotency_key": "req-proto-1",
    })
    payload = _last_proposal_payload()
    assert payload["account_no"] == "6222021234567891"
    assert payload["remark"] is None
    assert payload.get("enabled") is True


@pytest.mark.asyncio
async def test_company_info_save_proposal_args_bind_by_name(db, mcp_admin_auth, server):
    await server.call_tool("company_info_save_proposal", {
        "name": "澜铮鸿欣", "tax_id": "91310101MAELA36R35",
        "kind": "self", "is_default": True, "remark": "本公司",
    })
    payload = _last_proposal_payload()
    assert payload["name"] == "澜铮鸿欣"
    assert payload["tax_id"] == "91310101MAELA36R35"
    assert payload["kind"] == "self"
    assert payload["is_default"] is True
    assert payload["remark"] == "本公司"
    assert payload.get("bank_account") is None  # 幂等键/其它字段不得漂到这里


@pytest.mark.asyncio
async def test_confirm_execute_round_trip_over_protocol(db, mcp_admin_auth, server):
    """proposal → confirm 全链路经协议层走通一次（端到端冒烟）。"""
    from invoicing.models import User

    with SessionLocal() as s:
        admin = s.query(User).filter(User.role == "admin").order_by(User.id).first()
        assert admin is not None

    r = await server.call_tool("company_info_save_proposal", {
        "name": "冒烟公司", "tax_id": "91310101MAELA36R36", "kind": "supplier",
    })
    token = _result_json(r)["proposal_token"]

    r2 = await server.call_tool("confirm_execute", {
        "token": token, "tool_name": "company_info_save", "human_ack": True,
    })
    out = _result_json(r2)
    assert out["tax_id"] == "91310101MAELA36R36"
    assert out["name"] == "冒烟公司"

def test_no_proposal_wrapper_can_misalign_positionally(server):
    """结构性守卫：包装层未暴露的实现参数不得夹在已暴露参数之间。

    包装层按位置转发时，若实现函数有"未对外暴露"的参数夹在中间，
    其后所有参数整体错位（bank_account_save 的真 bug）。
    本测试对**全部 20 个** *_proposal 工具做静态校验，不依赖逐个冒烟。
    """
    import asyncio
    import inspect

    from invoicing.mcp import tools as mt

    tools = asyncio.run(server.list_tools())
    proposal_tools = [t for t in tools if t.name.endswith("_proposal")]
    assert len(proposal_tools) == 20

    for t in proposal_tools:
        impl = getattr(mt, t.name)
        impl_params = list(inspect.signature(impl).parameters)
        schema_props = list((t.input_schema or {}).get("properties", {}) or {})
        hidden = [p for p in impl_params if p not in schema_props]
        if not hidden:
            continue
        last_exposed_idx = max(
            (impl_params.index(p) for p in schema_props if p in impl_params),
            default=-1,
        )
        first_hidden_idx = min(impl_params.index(p) for p in hidden)
        assert first_hidden_idx > last_exposed_idx, (
            f"{t.name}: 未暴露参数 {hidden} 夹在已暴露参数之前/之间，"
            f"位置转发会整体错位（实现参数序：{impl_params}）"
        )
