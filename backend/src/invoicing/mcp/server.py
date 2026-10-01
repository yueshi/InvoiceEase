# invoicing/mcp/server.py
"""MCP Server 装配：39 个 Tool 注册 + 认证链路。

认证交给 SDK 内置栈（`auth=AuthSettings` + `token_verifier`）：
`AuthenticationMiddleware` → `AuthContextMiddleware` → `RequireAuthMiddleware`，
规范要求的 401 invalid_token / 403 insufficient_scope + WWW-Authenticate 由它负责，
我们只提供「令牌 → AccessToken」这一步（`MCPTokenVerifier`）。
设计见 design/2026-09-13-MCP身份与权限设计.md §4.1。
"""
from datetime import date

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from invoicing.config import settings
from invoicing.mcp import tools as mcp_tools
from invoicing.mcp.verifier import MCPTokenVerifier
from invoicing.schemas.company_info import CompanyInfoOut
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut


def _auth_settings() -> AuthSettings:
    """阶段 1 只需 resource_server_url（构造 WWW-Authenticate 的元数据地址）；
    issuer_url 是为满足 AuthSettings 必填约束，阶段 2 接 IdP 时才真正启用。
    required_scopes 默认留空——权限全部由工具级 scope 承担（见设计 R2）。"""
    scopes = [s.strip() for s in (settings.mcp_required_scopes or "").split(",") if s.strip()]
    return AuthSettings(
        issuer_url=settings.mcp_issuer_url,
        resource_server_url=settings.mcp_resource_url,
        required_scopes=scopes or None,
    )


def build_server() -> MCPServer:
    server = MCPServer("发票易", auth=_auth_settings(), token_verifier=MCPTokenVerifier())

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
        expense_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> InvoiceListResponse:
        return mcp_tools.list_invoices_mcp(
            status, date_from, date_to, keyword, expense_type, page, page_size
        )

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

    @server.tool(description="导入本地电子发票原件（XML/数电 OFD/PDF）入库：原件归档 → 解析 → 验真，返回发票记录（重复发票返回已拦截状态）。仅接受电子发票原件；银行回单等非发票文档请改用 receipt_ingest，拍照/截图件不支持。")
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

    @server.tool(description="创建报销单（草稿）：claim_type 选单据类型（travel 差旅/procurement 采购/entertainment 招待/office 办公/welfare 福利/other 其他），事项默认继承。随后 expense_add_entry 建事项、expense_add_invoices 加票、expense_submit 提交。")
    def expense_create(title: str, remark: str | None = None, claim_type: str | None = None) -> dict:
        return mcp_tools.expense_create(title, remark, claim_type)

    @server.tool(description="新建报销事项（费用明细行）：travel 差旅（需城市+起止日期）/ procurement 采购 / entertainment 招待（需对象+人数）/ office / other。凭证挂到事项下。")
    def expense_add_entry(claim_id: int, entry_type: str, title: str,
                          occurred_on: str | None = None, scene_fields: dict | None = None,
                          note: str | None = None) -> dict:
        return mcp_tools.expense_add_entry(claim_id, entry_type, title, occurred_on, scene_fields, note)

    @server.tool(description="按发票号码批量加入报销单的某个事项（自动校验一票一报/已验真/未拦截/归属范围），返回逐条结果。")
    def expense_add_invoices(claim_id: int, entry_id: int, invoice_numbers: list[str],
                             expense_type: str = "other", note: str | None = None) -> dict:
        return mcp_tools.expense_add_invoices(claim_id, entry_id, invoice_numbers, expense_type, note)

    @server.tool(description="把银行回单/缴款书回单挂为报销凭证（金额取自回单，一单一报）；receipt_id 用 receipt_list 查询，凭证类型留空按回单交易性质自动建议。")
    def expense_add_receipt(claim_id: int, entry_id: int, receipt_id: int,
                            voucher_type: str | None = None, expense_type: str = "other",
                            note: str | None = None) -> dict:
        return mcp_tools.expense_add_receipt(claim_id, entry_id, receipt_id, voucher_type, expense_type, note)

    @server.tool(description="录入无票支出人工凭证（receipt_voucher 收款凭证需收款人姓名+身份证号且 ≤500 元；contract 合同类；overseas 境外票据），返回 deductible 与不可扣除原因。")
    def expense_add_voucher(claim_id: int, entry_id: int, voucher_type: str, amount: str,
                            expense_type: str = "other", note: str | None = None,
                            payee_name: str | None = None, payee_id_no: str | None = None) -> dict:
        return mcp_tools.expense_add_voucher(claim_id, entry_id, voucher_type, amount,
                                             expense_type, note, payee_name, payee_id_no)

    @server.tool(description="提交报销单进入审批（需已有明细）。")
    def expense_submit(claim_id: int) -> dict:
        return mcp_tools.expense_submit(claim_id)

    @server.tool(description="报销单列表（status 可选 draft/pending_approval/approved/rejected/withdrawn；claim_type 可选单据类型）。")
    def expense_list(status: str | None = None, claim_type: str | None = None) -> list[dict]:
        return mcp_tools.expense_list(status, claim_type)

    @server.tool(description="审批报销单：action=approve/reject（驳回必填 reason）。")
    def expense_approve(claim_id: int, action: str = "approve", reason: str | None = None) -> dict:
        return mcp_tools.expense_approve(claim_id, action, reason)

    @server.tool(description="可报销发票池（已验真、未拦截、未占用），供选票建单。")
    def expense_eligible_invoices(limit: int = 50) -> list[dict]:
        return mcp_tools.expense_eligible_invoices(limit)

    @server.tool(description="常用企业银行账号列表（本司账户；回单解析判定「本司账户行」用——账号命中时对方户名留空并待核对）。")
    def bank_account_list() -> list[dict]:
        return mcp_tools.bank_account_list()

    @server.tool(description="保存本司银行账号（同账号更新；账号 6-32 位数字，自动去空格/连字符；is_default 设默认清其他默认）。")
    def bank_account_save(
        account_no: str,
        account_name: str | None = None,
        bank_name: str | None = None,
        remark: str | None = None,
        is_default: bool = False,
        enabled: bool = True,
    ) -> dict:
        return mcp_tools.bank_account_save(
            account_no, account_name, bank_name, remark, is_default, enabled
        )

    @server.tool(description="删除本司银行账号。")
    def bank_account_delete(id: int) -> dict:
        return mcp_tools.bank_account_delete(id)

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

    @server.tool(description="人工放行被拦截发票（blocked → 待复核，清除重复标记）。")
    def invoice_unblock(invoice_id: int) -> InvoiceOut:
        return mcp_tools.invoice_unblock(invoice_id)

    @server.tool(description="发票费用归类（不传 expense_type 时自动建议：travel/office/entertainment/procurement/other）。")
    def invoice_classify(
        invoice_id: int,
        expense_type: str | None = None,
        cost_center: str | None = None,
        description: str | None = None,
    ) -> InvoiceOut:
        return mcp_tools.invoice_classify(invoice_id, expense_type, cost_center, description)

    @server.tool(description="生成/重算发票复核预判（approve/reject/uncertain + 理由 + 置信度；建议不自动执行）。")
    def invoice_ai_review(invoice_id: int) -> InvoiceOut:
        return mcp_tools.invoice_ai_review(invoice_id)

    @server.tool(description="月度成本报表摘要（总额/张数/类型与部门分布；month 格式 YYYY-MM）。")
    def invoice_report(month: str) -> str:
        return mcp_tools.invoice_report(month)

    @server.tool(description="月度成本结构化统计（month 格式 YYYY-MM）：总额/不含税/税额/张数 + 费用类型分布(by_type) + 部门分布(by_center)。做图表与统计回答必须用本工具取数；单张发票明细请用 invoice_list。")
    def invoice_stats(month: str) -> dict:
        return mcp_tools.invoice_stats(month)

    @server.tool(description="导入已开票（销项发票，外部开票系统执行）：XML/OFD/PDF 文件解析入库，用于与收款回单对账；红字票自动关联原蓝票。")
    def sales_invoice_import(file_path: str) -> dict:
        return mcp_tools.sales_invoice_import(file_path)

    @server.tool(description="导入已开票（清单批量）：开票系统导出的 CSV/Excel；含「原发票号码」列时红票自动关联蓝票。")
    def sales_invoice_import_list(file_path: str) -> dict:
        return mcp_tools.sales_invoice_import_list(file_path)

    @server.tool(description="未关联原蓝票的红字票清单（销项退款待人工补关联）。")
    def red_invoice_list() -> list[dict]:
        return mcp_tools.red_invoice_list()

    @server.tool(description="人工补关联红字票与原蓝票（自动关联失败时使用）。")
    def red_invoice_link(red_invoice_id: int, original_invoice_id: int) -> dict:
        return mcp_tools.red_invoice_link(red_invoice_id, original_invoice_id)

    @server.tool(description="银行回单入库（PDF/图片，异步批次模式）：立即返回批次号，后台解析（规则+LLM，一份 PDF 可含多张回单）+ 自动配对发票建议。稍后用 receipt_upload_status 轮询进度；完成后 receipt_list 查看。同一文件重复提交会被拒绝。")
    def receipt_ingest(file_path: str) -> dict:
        return mcp_tools.receipt_ingest(file_path)

    @server.tool(description="查询回单上传批次解析状态（receipt_ingest 的配套轮询工具）：parsing/parsed/failed + 入库张数。")
    def receipt_parse_status(upload_id: int) -> dict:
        return mcp_tools.receipt_upload_status(upload_id)

    @server.tool(description="银行回单清单（month 格式 YYYY-MM）。")
    def receipt_list(month: str) -> list[dict]:
        return mcp_tools.receipt_list(month)

    @server.tool(description="手动配对回单与发票（覆盖自动建议）。")
    def receipt_pair(receipt_id: int, invoice_id: int) -> dict:
        return mcp_tools.receipt_pair(receipt_id, invoice_id)

    @server.tool(description="回单/无票费用汇报（总额/张数 + 无票支出清单，供催票）。")
    def receipt_report(month: str) -> str:
        return mcp_tools.receipt_report(month)

    @server.tool(description="月度健康报告（收票/验真/异常/成本/无票/数字员工改判，month 格式 YYYY-MM）。")
    def invoice_health_report(month: str) -> str:
        return mcp_tools.invoice_health_report(month)

    return server


mcp = build_server()
