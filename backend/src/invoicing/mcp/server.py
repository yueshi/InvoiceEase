# invoicing/mcp/server.py
"""MCP Server 装配：3 个 MVP Tool 注册。"""
from datetime import date

from mcp.server.mcpserver import MCPServer

from invoicing.mcp import tools as mcp_tools
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut


def build_server() -> MCPServer:
    server = MCPServer("发票易")

    @server.tool(
        description="手动触发邮箱轮询收取发票，返回收取结果统计（收到/拒收/忽略/重复/错误）。mailbox_id 缺省收取全部启用邮箱。",
    )
    def invoice_fetch(mailbox_id: int | None = None) -> PollResultOut:
        return mcp_tools.fetch_invoices(mailbox_id)

    @server.tool(
        description="查询发票列表：按状态/开票日期区间/关键词（发票号码或购销方名称）筛选，分页返回。",
    )
    def invoice_list(
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> InvoiceListResponse:
        return mcp_tools.list_invoices_mcp(status, date_from, date_to, keyword, page, page_size)

    @server.tool(description="查看单张发票完整信息（结构化字段+状态+验真结果）。")
    def invoice_detail(invoice_id: int) -> InvoiceOut:
        return mcp_tools.get_invoice_mcp(invoice_id)

    return server


mcp = build_server()
