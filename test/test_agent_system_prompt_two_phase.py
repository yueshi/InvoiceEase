"""Web 助手提示词必须与新协议一致（P0-2 final review 发现）。

此前 prompt 仍教 Agent 调 `expense_submit` / `invoice_delete` 等已改名的工具，
且明说「其余新增/导入类可直接调用，不需要确认」——与两段握手直接冲突：
Agent 要么撞「未知工具」，要么调 *_proposal 后当作已完成，对用户谎报「已入库」。
"""
from invoicing.agent.system_prompts import DEFAULT_SYSTEM_PROMPT

WRITE_TOOLS = [
    "expense_create", "expense_add_entry", "expense_add_invoices",
    "expense_add_receipt", "expense_add_voucher", "expense_submit",
    "expense_approve", "invoice_update", "invoice_delete", "invoice_unblock",
    "invoice_classify", "invoice_ai_review", "receipt_ingest", "receipt_pair",
    "sales_invoice_import", "red_invoice_link", "company_info_save",
    "company_info_delete", "bank_account_save", "bank_account_delete",
]


def test_prompt_mentions_two_phase_entrypoints():
    assert "confirm_execute" in DEFAULT_SYSTEM_PROMPT
    assert "human_ack" in DEFAULT_SYSTEM_PROMPT
    assert "_proposal" in DEFAULT_SYSTEM_PROMPT


def test_prompt_never_names_a_bare_write_tool():
    """写工具名后必须紧跟 `_proposal`（否则 Agent 会调不存在的工具）。"""
    import re

    for name in WRITE_TOOLS:
        for m in re.finditer(re.escape(name), DEFAULT_SYSTEM_PROMPT):
            tail = DEFAULT_SYSTEM_PROMPT[m.end():]
            assert tail.startswith("_proposal"), (
                f"提示词出现裸写工具名 {name}（位置 {m.start()}），"
                f"应写成 {name}_proposal"
            )


def test_prompt_forbids_calling_proposal_without_confirm():
    """必须明确：提案≠已执行，未 confirm 不得声称完成。"""
    assert "不落库" in DEFAULT_SYSTEM_PROMPT or "不会真正" in DEFAULT_SYSTEM_PROMPT