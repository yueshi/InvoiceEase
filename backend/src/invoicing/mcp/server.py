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

    from invoicing.mcp import extract as mcp_extract
    from invoicing.schemas.mcp_extract import ExtractResult, ValidationResult

    @server.tool(description="识别本地发票文件（PDF/OFD/XML 原件；图片按合规拒收），返回结构化数据与校验结果。")
    def extract_invoice(file_path: str) -> ExtractResult:
        return mcp_extract.extract_invoice_file(file_path)

    @server.tool(description="批量识别本地发票文件，逐条返回 success/error，互不影响。")
    def batch_extract_invoices(file_paths: list[str]) -> list[ExtractResult]:
        return mcp_extract.batch_extract_invoice_files(file_paths)

    @server.tool(description="校验发票数据：字段完整性与价税合计/大小写金额一致性。")
    def validate_invoice(invoice_data: dict) -> ValidationResult:
        return mcp_extract.validate_invoice_data(invoice_data)

    return server


mcp = build_server()
