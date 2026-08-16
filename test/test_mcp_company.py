"""MCP 常用公司工具测试（直调函数 + in-memory 协议）。"""
import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp.server import mcp
from invoicing.mcp.tools import company_info_delete, company_info_list, company_info_save
from invoicing.models import AuditLog


def test_save_and_list(db):
    saved = company_info_save(
        name="澜铮鸿欣（上海）数字科技有限公司",
        tax_id="91310101MAELA36R35",
        kind="self",
        is_default=True,
    )
    assert saved.name == "澜铮鸿欣（上海）数字科技有限公司"
    items = company_info_list(kind="self")
    assert len(items) == 1
    assert items[0].tax_id == "91310101MAELA36R35"


def test_save_upsert_by_tax_id(db):
    first = company_info_save(name="A", tax_id="91610132MA6UY02A5U")
    second = company_info_save(name="山东及时雨汽车科技有限公司西安分公司", tax_id="91610132MA6UY02A5U")
    assert first.id == second.id  # 同税号更新
    assert second.name == "山东及时雨汽车科技有限公司西安分公司"


def test_delete_ok(db):
    saved = company_info_save(name="A 公司", tax_id="91310101MAELA36R35")
    result = company_info_delete(saved.id)
    assert result == {"ok": True}
    assert company_info_list() == []


def test_delete_missing_raises(db):
    with pytest.raises(ValueError, match="记录不存在"):
        company_info_delete(99999)


def test_invalid_inputs_raise(db):
    # 税号长度不足
    with pytest.raises(ValueError, match="税号"):
        company_info_save(name="A", tax_id="123")
    # 税号含非法字符（小写）
    with pytest.raises(ValueError, match="税号"):
        company_info_save(name="A", tax_id="91310101maela36r35")
    # 非法 kind
    with pytest.raises(ValueError, match="非法类型"):
        company_info_save(name="A", tax_id="91310101MAELA36R35", kind="bad")
    # 空名称
    with pytest.raises(ValueError, match="不能为空"):
        company_info_save(name="  ", tax_id="91310101MAELA36R35")
    # is_default 仅 kind=self
    with pytest.raises(ValueError, match="is_default"):
        company_info_save(name="A", tax_id="91310101MAELA36R35", kind="supplier", is_default=True)


def test_default_clears_previous(db):
    company_info_save(name="A 公司", tax_id="91310101MAELA36R35", kind="self", is_default=True)
    company_info_save(name="B 公司", tax_id="91610132MA6UY02A5U", kind="self", is_default=True)
    defaults = [i for i in company_info_list(kind="self") if i.is_default]
    assert len(defaults) == 1
    assert defaults[0].name == "B 公司"


def test_kind_change_forces_default_off(db):
    saved = company_info_save(name="A 公司", tax_id="91310101MAELA36R35", kind="self", is_default=True)
    updated = company_info_save(name="A 公司", tax_id="91310101MAELA36R35", kind="supplier")
    assert updated.id == saved.id
    assert updated.kind == "supplier"
    assert updated.is_default is False  # kind 改非 self 强制清默认


def test_save_writes_audit(db):
    company_info_save(name="A 公司", tax_id="91310101MAELA36R35")
    # 工具函数内部用独立 SessionLocal 写审计并 commit，另开会话查询（与 test_mcp_tools 先例一致）
    with SessionLocal() as s:
        logs = (
            s.query(AuditLog)
            .filter(AuditLog.action == "CONFIG_CHANGE", AuditLog.channel == "mcp")
            .all()
        )
    assert len(logs) == 1
    assert logs[0].detail == {"entity": "company_info", "tax_id": "91310101MAELA36R35"}


@pytest.mark.asyncio
async def test_in_memory_client_lists_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"company_info_list", "company_info_save", "company_info_delete"} <= names
