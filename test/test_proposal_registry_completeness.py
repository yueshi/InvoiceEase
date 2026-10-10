"""P0-2 软白名单完整性：20 个写工具全部注册为可执行提案（v1.1 §7.5）。

Ruling（ledger Task 4）：plan 文字写 19，实际清单 20 个工具——以 20 为准。
"""
import asyncio

from invoicing.mcp import tools as _tools  # noqa: F401 —— 导入触发 @register_proposal 注册
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY

# 20 个两段式写工具（v1.1 §7.5：所有写 MCP 工具）
EXPECTED_TOOLS = {
    # 报销（7）
    "expense_create", "expense_add_entry", "expense_add_invoices",
    "expense_add_receipt", "expense_add_voucher", "expense_submit",
    "expense_approve",
    # 发票（5）
    "invoice_update", "invoice_delete", "invoice_unblock",
    "invoice_classify", "invoice_ai_review",
    # 回单/销项（4）
    "receipt_ingest", "receipt_pair",
    "sales_invoice_import", "red_invoice_link",
    # 主数据（4）
    "company_info_save", "company_info_delete",
    "bank_account_save", "bank_account_delete",
}


def test_registry_has_exactly_20_tools():
    assert len(PROPOSAL_REGISTRY) == 20, (
        f"两段式工具数变化（{len(PROPOSAL_REGISTRY)}），请同步本测试与设计文档"
    )


def test_registry_keys_match_spec():
    assert set(PROPOSAL_REGISTRY.keys()) == EXPECTED_TOOLS


def test_every_registered_executor_takes_db_first():
    """执行体签名约定：`(db, *, ...)` —— confirm_execute 传自己开的 session。"""
    import inspect

    for name, fn in PROPOSAL_REGISTRY.items():
        params = list(inspect.signature(fn).parameters.values())
        assert params, f"{name} 无参数"
        assert params[0].name == "db", f"{name} 首参不是 db：{params[0].name}"


def test_every_write_tool_has_a_proposal_counterpart():
    """server 注册的每个 *_proposal 工具都应在 registry 有对应执行体。

    反向核对：*_proposal MCP 工具名去掉后缀后必须命中 registry，
    防"注册了提案工具但忘了 register_proposal"（确认时才发现）。
    """
    from invoicing.mcp.server import build_server

    server = build_server()
    tools = asyncio.run(server.list_tools())
    proposal_tools = [t.name for t in tools if t.name.endswith("_proposal")]

    assert len(proposal_tools) == 20, f"*_proposal 工具数 = {len(proposal_tools)}"

    registry_names = set(PROPOSAL_REGISTRY.keys())
    for tool_name in proposal_tools:
        exec_name = tool_name[: -len("_proposal")]
        assert exec_name in registry_names, (
            f"{tool_name} 无对应执行体注册（{exec_name} 不在 PROPOSAL_REGISTRY）"
        )