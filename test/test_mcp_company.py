"""MCP 常用公司工具测试（直调函数 + in-memory 协议）。"""
import pytest
from mcp import Client

from invoicing.mcp.server import mcp
from invoicing.mcp.tools import company_info_list, company_info_save


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


@pytest.mark.asyncio
async def test_in_memory_client_lists_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"company_info_list", "company_info_save", "company_info_delete"} <= names
