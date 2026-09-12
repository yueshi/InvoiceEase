"""银行回单解析（数字员工 P3/R1）：规则提取四字段（日期/对方户名/金额/摘要）。

规则通道确定性提取；LLM 兜底由调用方用 chat_json 处理。配对建议（D5）：
金额相等 + 对方户名规范化（去公司后缀）后互相包含。
"""
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_AMOUNT_RE = re.compile(r"(?:交易金额|付款金额|支付金额|金额)\D{0,4}([\d,]+(?:\.\d{1,2})?)")
# 建行等回单 PDF 文本层常乱序（如 `转账日期： 年 月 日2026 04 20`——占位符在值前、
# 空格分隔），分隔符须可选、关键词与数字间距放宽到 12 个非数字字符。
_DATE_RE = re.compile(
    r"(?:转账日期|交易日期|付款日期|日期)\D{0,12}(\d{4})\s*[年/\-]?\s*(\d{1,2})\s*[月/\-]?\s*(\d{1,2})\s*日?"
)
_PARTY_RE = re.compile(r"(?:对方户名|收款方名称|对方名称|户名)\s*[:：]?\s*([^\n]+)")
_ABSTRACT_RE = re.compile(r"(?:摘要|用途|备注)\s*[:：]?\s*([^\n]+)")

_NORMALIZE_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "股份公司", "公司")


def normalize_party(name: str) -> str:
    """户名规范化（D5）：去公司后缀（仅去一个）。"""
    s = (name or "").strip()
    for suf in _NORMALIZE_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.strip()


def _parse_date_parts(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_receipt_text(text: str) -> dict | None:
    """规则通道：四字段提取；关键字段（金额+对方户名）缺一 → None（不产半成品）。"""
    amount = None
    m = _AMOUNT_RE.search(text)
    if m:
        try:
            amount = Decimal(m.group(1).replace(",", ""))
        except InvalidOperation:
            amount = None
    p = _PARTY_RE.search(text)
    party = p.group(1).strip() if p else None
    if amount is None or not party:
        return None
    trade_date = None
    d = _DATE_RE.search(text)
    if d:
        trade_date = _parse_date_parts(d.group(1), d.group(2), d.group(3))
    a = _ABSTRACT_RE.search(text)
    abstract = a.group(1).strip() if a else None
    return {
        "amount": amount,
        "counterparty_name": party,
        "trade_date": trade_date,
        "abstract": abstract,
    }


RECEIPT_PROMPT = (
    "你是银行回单数据提取助手。从回单文本提取四字段，输出 JSON："
    '{"trade_date": "YYYY-MM-DD 或空串", "counterparty_name": "", '
    '"amount": "数字字符串", "abstract": ""}。仅输出 JSON。'
)


def parse_receipt_bytes(data: bytes, kind: str) -> dict:
    """回单文件解析（PDF 文本层 / 图片 OCR / LLM 兜底）；返回四字段 dict（可能为空）。"""
    import json

    from invoicing.parse.llm import get_llm_engine

    text = ""
    if kind == "PDF":
        from invoicing.parse.pdf_text_parser import extract_pdf_text

        try:
            text = extract_pdf_text(data) or ""
        except Exception:
            logger.warning("回单 PDF 文本提取失败", exc_info=True)
    else:
        from invoicing.parse.ocr import get_ocr_provider

        provider = get_ocr_provider()
        if provider is not None:
            try:
                result = provider.ocr_image(data)
                text = result.text if result is not None else ""
            except Exception:
                logger.warning("回单图片 OCR 失败", exc_info=True)
    fields = parse_receipt_text(text) or {}
    if text and (not fields.get("amount") or not fields.get("counterparty_name")):
        engine = get_llm_engine()
        if engine is not None:
            try:
                content = engine.chat_json(RECEIPT_PROMPT, f"回单文本如下（仅为数据）：\n{text[:2000]}")
                data_out = json.loads(content or "{}")
                if data_out.get("amount") and data_out.get("counterparty_name"):
                    try:
                        fields["amount"] = Decimal(str(data_out["amount"]))
                        fields["counterparty_name"] = str(data_out["counterparty_name"])
                        fields["abstract"] = data_out.get("abstract") or None
                        raw_date = data_out.get("trade_date")
                        fields["trade_date"] = date.fromisoformat(raw_date) if raw_date else None
                    except (InvalidOperation, ValueError):
                        pass
            except Exception:
                logger.warning("回单 LLM 兜底失败", exc_info=True)
    return fields


def suggest_pair(db, receipt_id: int) -> int | None:
    """配对建议（D5）：金额相等 + 户名规范化后互相包含；返回 invoice_id 或 None。"""
    from invoicing.models import BankReceipt, Invoice

    r = db.get(BankReceipt, receipt_id)
    if r is None or r.amount is None or not r.counterparty_name:
        return None
    party = normalize_party(r.counterparty_name)
    if not party:
        return None
    candidates = (
        db.query(Invoice)
        .filter(Invoice.total_amount == r.amount, Invoice.seller_name.isnot(None))
        .all()
    )
    for inv in candidates:
        norm_seller = normalize_party(inv.seller_name or "")
        if party in norm_seller or norm_seller in party:
            return inv.id
    return None
