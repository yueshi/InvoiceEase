"""MCP 常用公司工具测试（直调函数 + in-memory 协议）。

P0-2 两段握手：写操作 = *_proposal（拿 token）→ confirm_execute（human_ack=true）。
本文件的 _save/_delete 是这两步的薄封装，保持各用例原有断言。
"""
import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp import tools as mt
from invoicing.mcp.server import mcp
from invoicing.models import AuditLog


@pytest.fixture(autouse=True)
def _admin_mcp_ctx(mcp_admin_auth):
    """本文件测工具**行为**（非身份）：默认以管理员身份调用，等价升级前的单令牌通道。
    身份/权限相关的回归见 test_mcp_permissions.py。"""


def _save(**kw):
    prop = mt.company_info_save_proposal(**kw)
    return mt.confirm_execute(token=prop["proposal_token"],
                              tool_name="company_info_save", human_ack=True)


def _delete(id: int):
    prop = mt.company_info_delete_proposal(id)
    return mt.confirm_execute(token=prop["proposal_token"],
                              tool_name="company_info_delete", human_ack=True)


def test_save_and_list(db):
    saved = _save(
        name="澜铮鸿欣（上海）数字科技有限公司",
        tax_id="91310101MAELA36R35",
        kind="self",
        is_default=True,
    )
    assert saved["name"] == "澜铮鸿欣（上海）数字科技有限公司"
    items = mt.company_info_list(kind="self")
    assert len(items) == 1
    assert items[0].tax_id == "91310101MAELA36R35"


def test_save_upsert_by_tax_id(db):
    first = _save(name="A", tax_id="91610132MA6UY02A5U")
    second = _save(name="山东及时雨汽车科技有限公司西安分公司", tax_id="91610132MA6UY02A5U")
    assert first["id"] == second["id"]  # 同税号更新
    assert second["name"] == "山东及时雨汽车科技有限公司西安分公司"


def test_save_proposal_zero_side_effect(db):
    """P0-2：提案阶段不落库。"""
    prop = mt.company_info_save_proposal(name="A", tax_id="91310101MAELA36R35")
    assert prop["proposal_token"]
    assert mt.company_info_list() == []


def test_delete_ok(db):
    saved = _save(name="A 公司", tax_id="91310101MAELA36R35")
    result = _delete(saved["id"])
    assert result == {"ok": True}
    assert mt.company_info_list() == []


def test_delete_missing_raises(db):
    prop = mt.company_info_delete_proposal(99999)
    with pytest.raises(ValueError, match="记录不存在"):
        mt.confirm_execute(token=prop["proposal_token"],
                           tool_name="company_info_delete", human_ack=True)


def test_invalid_inputs_raise(db):
    """非法输入在**提案阶段**被拒（不产生待确认提案，不浪费人工确认）。"""
    # 税号长度不足
    with pytest.raises(ValueError, match="税号"):
        mt.company_info_save_proposal(name="A", tax_id="123")
    # 税号含非法字符（小写）
    with pytest.raises(ValueError, match="税号"):
        mt.company_info_save_proposal(name="A", tax_id="91310101maela36r35")
    # 非法 kind
    with pytest.raises(ValueError, match="非法类型"):
        mt.company_info_save_proposal(name="A", tax_id="91310101MAELA36R35", kind="bad")
    # 空名称
    with pytest.raises(ValueError, match="不能为空"):
        mt.company_info_save_proposal(name="  ", tax_id="91310101MAELA36R35")
    # is_default 仅 kind=self
    with pytest.raises(ValueError, match="is_default"):
        mt.company_info_save_proposal(name="A", tax_id="91310101MAELA36R35",
                                      kind="supplier", is_default=True)


def test_default_clears_previous(db):
    _save(name="A 公司", tax_id="91310101MAELA36R35", kind="self", is_default=True)
    _save(name="B 公司", tax_id="91610132MA6UY02A5U", kind="self", is_default=True)
    defaults = [i for i in mt.company_info_list(kind="self") if i.is_default]
    assert len(defaults) == 1
    assert defaults[0].name == "B 公司"


def test_kind_change_forces_default_off(db):
    saved = _save(name="A 公司", tax_id="91310101MAELA36R35", kind="self", is_default=True)
    updated = _save(name="A 公司", tax_id="91310101MAELA36R35", kind="supplier")
    assert updated["id"] == saved["id"]
    assert updated["kind"] == "supplier"
    assert updated["is_default"] is False  # kind 改非 self 强制清默认


def test_save_writes_audit(db):
    _save(name="A 公司", tax_id="91310101MAELA36R35")
    # 工具函数内部用独立 SessionLocal 写审计并 commit，另开会话查询（与 test_mcp_tools 先例一致）
    with SessionLocal() as s:
        logs = (
            s.query(AuditLog)
            .filter(AuditLog.action == "CONFIG_CHANGE", AuditLog.channel == "mcp")
            .all()
        )
    assert len(logs) == 1
    # detail 除业务字段外还带审计主体（谁 / 哪个令牌 / 哪个租户）——MCP 身份设计 §4.5
    assert logs[0].detail["entity"] == "company_info"
    assert logs[0].detail["tax_id"] == "91310101MAELA36R35"
    assert logs[0].detail["token_source"] == "token"
    assert logs[0].user_id is not None  # 主体落到人（此前为 None）


@pytest.mark.asyncio
async def test_in_memory_client_lists_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    # P0-2：写工具以 *_proposal 暴露
    assert {"company_info_list", "company_info_save_proposal",
            "company_info_delete_proposal"} <= names