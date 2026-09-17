"""回单领域规则：交易性质分类、无票支出判定（列表筛选 / 周期探测 / 月报 / MCP 催票共用）。"""
import re

from invoicing.models import BankReceipt

# 交易性质 → 展示名 / 发票要求 / 报销凭证类型建议
# 发票要求：fetch=需取得发票（催票）；issue=需我方开具（销项对账）；none=无需发票
CATEGORY_META: dict[str, dict] = {
    "tax": {"label": "税费缴款", "requirement": "none", "voucher_type": "tax_receipt"},
    "social": {"label": "社保/公积金", "requirement": "none", "voucher_type": "tax_receipt"},
    "bank_fee": {"label": "银行内部费用", "requirement": "none", "voucher_type": "bank_receipt"},
    "salary": {"label": "工资代发", "requirement": "none", "voucher_type": "internal"},
    "internal_transfer": {"label": "本司账户调拨", "requirement": "none", "voucher_type": "internal"},
    "sales_collection": {"label": "客户回款", "requirement": "issue", "voucher_type": "bank_receipt"},
    "treasury_in": {"label": "财政/补贴/利息收入", "requirement": "none", "voucher_type": "bank_receipt"},
    "purchase": {"label": "对外采购/服务支出", "requirement": "fetch", "voucher_type": "bank_receipt"},
    "unknown": {"label": "待定", "requirement": "fetch", "voucher_type": "bank_receipt"},
}

# 机构名模式收窄（设计 §2 误判边界）：「税务师事务所」这类含"税务"的服务商不得命中。
# 国库真实措辞两种（「国库」「国家金库××支库」「中国人民银行国库」）都要覆盖 → 国库|金库
_RE_TAX_ORG = re.compile(r"国库|金库|税务局|税务分局|财政局|财政部|海关")
# 社保：机构名之外，税局代征的险种名出在摘要里——五险（养老/医疗/失业/工伤/生育）全列
_RE_SOCIAL = re.compile(
    r"社会保险|社保|养老保险|医疗保险|失业保险|工伤保险|生育保险|住房公积金|公积金管理中心"
)
_RE_FEE = re.compile(r"手续费|工本费|服务费|年费|账户管理费|利息|结息")
_RE_SALARY = re.compile(r"代发工资|工资代发|薪资|工资|奖金")
_RE_TRANSFER = re.compile(r"调拨|内部划转|资金归集|转存")


def _hit(pattern: re.Pattern, *values: str | None) -> bool:
    return any(pattern.search(v or "") for v in values)


def requirement_of(category: str | None) -> str:
    """发票要求（派生，不落库）：未知类别按「需取得发票」保守处理。"""
    return CATEGORY_META.get(category or "unknown", CATEGORY_META["unknown"])["requirement"]


def suggest_voucher_type(category: str | None) -> str:
    """报销凭证类型建议（税费/社保 → 缴款书回单；其余 → 银行回单/内部凭证）。"""
    return CATEGORY_META.get(category or "unknown", CATEGORY_META["unknown"])["voucher_type"]


def classify_receipt(r: BankReceipt, self_names: set[str] | None = None) -> tuple[str, str]:
    """规则判定交易性质（顺序即优先级，命中即止）。纯函数，不查库。

    self_names 为本司名称集合（`parse.receipt.self_name_set` 的产物，已规范化），
    用于识别「本司账户间调拨」。
    """
    from invoicing.parse.receipt import normalize_party

    party = r.counterparty_name or ""
    abstract = r.abstract or ""
    self_names = self_names or set()
    norm_party = normalize_party(party)

    if _hit(_RE_SOCIAL, party, abstract):
        return "social", "rule"  # 社保费常由税务局代征 → 排在税务之前
    if _hit(_RE_TAX_ORG, party):
        return ("treasury_in" if r.direction == "收" else "tax"), "rule"
    if _hit(_RE_FEE, abstract):
        return "bank_fee", "rule"
    if _hit(_RE_SALARY, abstract):
        return "salary", "rule"
    if (
        "self_account_row" in (r.quality_issues or [])
        or _hit(_RE_TRANSFER, abstract)
        or any(n and (n in norm_party or norm_party in n) for n in self_names if norm_party)
    ):
        return "internal_transfer", "rule"
    if r.direction == "收":
        return "sales_collection", "rule"
    if r.direction == "付":
        return "purchase", "rule"
    return "unknown", "rule"


def reclassify_receipts(db, include_manual: bool = False) -> int:
    """按规则重算交易性质（幂等；默认跳过人工覆盖行）。返回变更行数。"""
    from invoicing.parse.receipt import self_name_set

    names = self_name_set(db)
    q = db.query(BankReceipt)
    if not include_manual:
        q = q.filter(BankReceipt.category_source != "manual")
    changed = 0
    for r in q.all():
        cat, src = classify_receipt(r, names)
        if r.category != cat or r.category_source != src:
            r.category = cat
            r.category_source = src
            changed += 1
    db.commit()
    return changed


def is_unpaired_outflow(receipt: BankReceipt) -> bool:
    """未配对 + 非收方向——「未配对队列」的谓词本体（一处判定，多方共用）。

    支出方向：direction 为 "付" 或未识别（None）——与凭证导出同口径
    （reports.receipts_to_csv：仅 "收" 走借银行/贷收入，其余按费用处理）。
    收款（"收"）未配对属「收款未开票」，不是支出，不计入。
    无票支出（is_unmatched_expense）与月报「无需发票」行（reports.monthly_health）
    必须共用本函数——此前月报逐字重抄，谓词一改就漂移。
    """
    return receipt.paired_invoice_id is None and receipt.direction != "收"


def is_unmatched_expense(receipt: BankReceipt) -> bool:
    """无票支出 = 未配对 + 非收方向（兜底未重分类的历史行）+ 该交易性质需要取得发票。

    税费/社保/银行费用/工资/内部调拨等 requirement=none 的类别不计入（设计 §6）。
    """
    return is_unpaired_outflow(receipt) and requirement_of(receipt.category) == "fetch"
