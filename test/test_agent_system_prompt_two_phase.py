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

# ---- P2：结构化卡片契约 -----------------------------------------------------

def test_prompt_documents_card_fences():
    """出卡契约必须在提示词里——否则模型不输出卡，前端组件永远不触发。"""
    p = DEFAULT_SYSTEM_PROMPT
    assert "expense-draft" in p
    assert "anomaly" in p


def test_prompt_has_multiturn_pacing():
    """多轮补齐节奏：每轮只问 1-2 个问题（spec §6.2）。"""
    p = DEFAULT_SYSTEM_PROMPT
    assert "1-2" in p or "1～2" in p or "一到两个" in p


def test_prompt_says_card_click_is_not_confirmation():
    """点卡片按钮 = 用户说了句话，**不等于**已确认执行——写操作仍走两段握手。"""
    p = DEFAULT_SYSTEM_PROMPT
    assert "卡片" in p
    assert "不等于" in p or "≠" in p


def test_prompt_never_allows_skipping_confirm():
    """提示词不得出现任何「可跳过确认」的放行语（比查具体词更能抓住漏网写法）。"""
    p = DEFAULT_SYSTEM_PROMPT
    for phrase in ("无需确认", "不需确认", "免确认", "可跳过确认", "直接提交", "自动提交"):
        assert phrase not in p, f"提示词出现放行语：{phrase}"
