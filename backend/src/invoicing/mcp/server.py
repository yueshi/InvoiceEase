# invoicing/mcp/server.py
"""MCP Server 装配：10 个 Tool 注册（收取/查询/详情 + WorkBuddy 识别/校验/归档 + 常用公司管理）。"""
from datetime import date

from mcp.server.mcpserver import MCPServer

from invoicing.mcp import tools as mcp_tools
from invoicing.schemas.company_info import CompanyInfoOut
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

    @server.tool(description="导入本地发票原件入库：原件归档 → 解析 → 验真，返回发票记录（重复发票返回已拦截状态）。")
    def invoice_ingest(file_path: str) -> InvoiceOut:
        return mcp_tools.ingest_invoice(file_path)

    @server.tool(description="查询常用税号及公司信息（kind 可选 self/supplier/other）。")
    def company_info_list(kind: str | None = None) -> list[CompanyInfoOut]:
        return mcp_tools.company_info_list(kind)

    @server.tool(description="保存常用税号及公司信息（同税号更新；kind=self 可设默认）。")
    def company_info_save(
        name: str, tax_id: str, kind: str = "other", is_default: bool = False, remark: str | None = None
    ) -> CompanyInfoOut:
        return mcp_tools.company_info_save(name, tax_id, kind, is_default, remark)

    @server.tool(description="删除常用税号及公司信息。")
    def company_info_delete(id: int) -> dict:
        return mcp_tools.company_info_delete(id)

    @server.tool(description="更新发票业务字段（人工复核纠正用；仅传入字段生效，状态变更走 review/verify）。")
    def invoice_update(
        invoice_id: int,
        invoice_number: str | None = None,
        issue_date: str | None = None,
        amount_without_tax: str | None = None,
        tax_amount: str | None = None,
        total_amount: str | None = None,
        total_amount_cn: str | None = None,
        seller_name: str | None = None,
        seller_tax_id: str | None = None,
        buyer_name: str | None = None,
        buyer_tax_id: str | None = None,
        invoice_type: str | None = None,
        review_note: str | None = None,
    ) -> InvoiceOut:
        return mcp_tools.invoice_update(
            invoice_id, invoice_number, issue_date, amount_without_tax, tax_amount,
            total_amount, total_amount_cn, seller_name, seller_tax_id, buyer_name,
            buyer_tax_id, invoice_type, review_note,
        )

    @server.tool(description="删除发票（审计全字段快照 + 原件清理）。")
    def invoice_delete(invoice_id: int) -> dict:
        return mcp_tools.invoice_delete(invoice_id)

    return server


mcp = build_server()
