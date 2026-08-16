"""版式发票文本层规则提取（标签锚定优先 + 通用数字兜底）。

真实布局实测（design/2026-08-16-layout-invoice-parsing-design.md §一）：
- 标准布局：`发票号码：xxx` / `名称：xxx` 标签与值相邻
- 破碎布局：标签块与值块分离（pypdf 文本抽取顺序所致）——兜底用
  全局首个 20 位数字（号码）、首个年月日（开票日期）、全部 18 位税号按序（购买方=第一个）

关键字段 = 号码 + 开票日期 + 不含税 + 税额 + 价税合计；任一缺失 → None。
购销方名称在破碎布局中可能不可靠提取，允许缺失（置信度 0.85 已表达不确定性）。
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from invoicing.parse.schemas import ParsedInvoice

TEXT_CONFIDENCE = 0.85

_LABEL_NO = re.compile(r"发票号码\s*[:：]?\s*(\d{20}|\d{8})")
_LABEL_DATE_CN = re.compile(r"开票日期\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_LABEL_DATE_ISO = re.compile(r"开票日期\s*[:：]?\s*(\d{4}-\d{1,2}-\d{1,2})")
_LABEL_NAME = re.compile(r"名称\s*[:：]\s*([^\s:：，,]+)")
_LABEL_TAX_ID = re.compile(r"统一社会信用代码/纳税人识别号\s*[:：]\s*([0-9A-Z]{18})")
# ¥ 与数字间、两个 ¥ 之间仅限水平空白（不跨行）：防止无开票人时
# 「¥1270.00\n¥1198.11」把价税合计与明细不含税错配成一对
_LABEL_TOTAL = re.compile(r"合\s*计.*?¥[ \t]*([\d.]+)[ \t]*¥[ \t]*([\d.]+)", re.S)
_LABEL_CN = re.compile(r"价税合计\s*[（(]大写[)）]\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元圆整角分]+)")
_AMOUNTS = re.compile(r"\d+\.\d{2}")
_GENERIC_NO = re.compile(r"(\d{20,})")  # 20 位票号；粘连场景（票号+日期）取前 20 位
_GENERIC_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_GENERIC_TAX_IDS = re.compile(r"[0-9A-Z]{18}")
# 非贪婪到第一个「公司」结尾；(?<![0-9]) 防「09日澜铮鸿欣…」把日期尾字吞进名称
# 后缀分支仅允许「分公司/支公司/子公司/分理处/营业部」类特定词——防误吞两个公司名
_COMPANY = re.compile(
    r"(?<![0-9])[\u4e00-\u9fa5（()）·]{2,30}?公司(?:[\u4e00-\u9fa5]{1,8}?(?:分公司|支公司|子公司|分理处|营业部))?"
)


def _to_date(m) -> str | None:
    groups = m.groups()
    if len(groups) == 3:
        return date(int(groups[0]), int(groups[1]), int(groups[2])).isoformat()
    if len(groups) == 1 and "-" in groups[0]:
        return date.fromisoformat(groups[0]).isoformat()
    return None


def extract_fields_from_text(text: str, confidence: float = TEXT_CONFIDENCE) -> ParsedInvoice | None:
    text = text.replace("　", " ").replace("￥", "¥")  # 全角空格与全角人民币符号归一
    # OCR 数字内空格归一（"65. 48" → "65.48"）——仅折叠水平空白（空格/制表符），
    # 不得跨换行（否则金额行尾与下一行日期数字粘连，如 ¥71.89\n2026 → 71.892026）
    text = re.sub(r"(?<=\d)[ \t]+(?=\d)", "", text)
    text = re.sub(r"(?<=\d)[ \t]+(?=\.)|(?<=\.)[ \t]+(?=\d)", "", text)

    number = None
    m = _LABEL_NO.search(text)
    if m:
        number = m.group(1)
    else:
        m = _GENERIC_NO.search(text)
        if m:
            number = m.group(1)[:20]

    issue_date = None
    for m in (_LABEL_DATE_CN.search(text), _LABEL_DATE_ISO.search(text), _GENERIC_DATE_CN.search(text)):
        if m:
            issue_date = _to_date(m)
            break

    total_m = _LABEL_TOTAL.search(text)
    amount_without_tax = tax_amount = total_amount = None
    if total_m:
        try:
            amount_without_tax = Decimal(total_m.group(1))
            tax_amount = Decimal(total_m.group(2))
            total_amount = amount_without_tax + tax_amount
        except InvalidOperation:
            amount_without_tax = tax_amount = total_amount = None
    if total_amount is None:
        # 兜底：无 ¥ 符号的合计行——% 锚定：右侧取税额，紧邻 % 的数字串逐后缀
        # 试税率（如 "126.883%3.81" 粘连场景中税率是最后一个数字 "3"），
        # 再向左找满足税率自洽（税额 ≈ 金额 × 税率）的金额
        for m in re.finditer("%", text):
            before = text[: m.start()]
            after = text[m.end():]
            tax_m = re.search(r"(\d+\.\d{2})", after)
            if not tax_m:
                continue
            try:
                tax = Decimal(tax_m.group(1))
            except InvalidOperation:
                continue
            run_m = re.search(r"(\d+(?:\.\d+)?)$", before)
            if not run_m:
                continue
            run = run_m.group(1)
            for i in range(1, len(run) + 1):
                cand = run[-i:]
                if cand.startswith("."):
                    continue
                try:
                    rate = Decimal(cand)
                except InvalidOperation:
                    continue
                if not (0 < rate < 100):
                    continue
                prefix = before[: len(before) - i]
                for am in reversed(_AMOUNTS.findall(prefix)):
                    amt = Decimal(am)
                    if abs(tax - amt * rate / Decimal(100)) <= Decimal("0.05"):
                        amount_without_tax, tax_amount, total_amount = amt, tax, amt + tax
                        break
                if total_amount is not None:
                    break
            if total_amount is not None:
                break

    if not number or not issue_date or total_amount is None:
        return None

    buyer_name = seller_name = buyer_tax_id = seller_tax_id = None
    # 过滤「名称：」标签后紧跟「统一社会信用代码/…」标签的误捕（破碎布局标签连排）
    names = [n for n in _LABEL_NAME.findall(text) if "统一社会信用代码" not in n]
    tax_ids = _LABEL_TAX_ID.findall(text)
    if names and tax_ids and len(names) >= 2 and len(tax_ids) >= 2:
        buyer_name, seller_name = names[0], names[1]
        buyer_tax_id, seller_tax_id = tax_ids[0], tax_ids[1]
    else:
        # 排除发票号码的 18 位前缀被误捕为税号（20 位票号场景）
        generic_ids = [t for t in _GENERIC_TAX_IDS.findall(text) if not (number and t in number)]
        if len(generic_ids) >= 2:
            buyer_tax_id, seller_tax_id = generic_ids[0], generic_ids[1]
            # 公司名兜底：文本中「…公司」按出现序配对（购买方在前、销售方在后，
            # 真机实测与版式惯例一致；提取不到仍允许留空，置信度已表达不确定性）
            companies = _COMPANY.findall(text)
            if len(companies) >= 2:
                buyer_name, seller_name = companies[0], companies[1]

    total_cn = None
    m = _LABEL_CN.search(text)
    if m:
        total_cn = m.group(1)

    return ParsedInvoice(
        invoice_number=number,
        issue_date=date.fromisoformat(issue_date),
        amount_without_tax=amount_without_tax,
        tax_amount=tax_amount,
        total_amount=total_amount,
        total_amount_cn=total_cn or "",
        seller_name=seller_name or "",
        seller_tax_id=seller_tax_id or "",
        buyer_name=buyer_name or "",
        buyer_tax_id=buyer_tax_id or "",
        invoice_type=None,
        confidence_score=confidence,
        parse_source="PDF_TEXT",
    )
