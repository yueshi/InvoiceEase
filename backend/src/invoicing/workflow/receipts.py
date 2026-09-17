"""回单领域规则：无票支出判定（列表筛选 / 周期探测 / 月报 / MCP 催票共用一处）。"""
from invoicing.models import BankReceipt


def is_unmatched_expense(receipt: BankReceipt) -> bool:
    """无票支出 = 支出方向且未配对发票。

    支出方向：direction 为 "付" 或未识别（None）——与凭证导出同口径
    （reports.receipts_to_csv：仅 "收" 走借银行/贷收入，其余按费用处理）。
    收款（"收"）未配对属「收款未开票」，不是无票支出，不计入。
    """
    return receipt.paired_invoice_id is None and receipt.direction != "收"
