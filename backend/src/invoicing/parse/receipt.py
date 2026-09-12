"""银行回单解析（数字员工 P3/R1）：规则提取四字段（日期/对方户名/金额/摘要）。

规则通道确定性提取；LLM 兜底由调用方用 chat_json 处理。配对建议（D5）：
金额相等 + 对方户名规范化（去公司后缀）后互相包含。
"""
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# 金额两组件：①金额关键词（禁止跨行——避免「…金额\n<产品编号>」表头误配）
# ②￥ 货币符号锚定（兜「金额 （大写）人民币… （小写）￥1,600.00」这类关键词与
# 数字间距超限的版式）。
_AMOUNT_RE = re.compile(
    r"(?:交易金额|付款金额|支付金额|金额)[^0-9\n]{0,4}([\d,]+(?:\.\d{1,2})?)"
    r"|￥\s*([\d,]+(?:\.\d{1,2})?)"
)
# 建行等回单 PDF 文本层常乱序（如 `转账日期： 年 月 日2026 04 20`——占位符在值前、
# 空格分隔），分隔符须可选、关键词与数字间距放宽到 12 个非数字字符。
_DATE_RE = re.compile(
    r"(?:转账日期|交易日期|付款日期|日期)\D{0,12}(\d{4})\s*[年/\-]?\s*(\d{1,2})\s*[月/\-]?\s*(\d{1,2})\s*日?"
)
# 户名关键词含税票回单版式（收款国库/征收机关=收款方；付款人全称是本司不能当对方）。
# 关键词与冒号间允许 ≤12 字的标签尾巴（如「征收机关名称（委托方）：」），但仅在
# 确有冒号时消耗——无冒号行（`对方户名 北京某某公司`）直接从关键词后取值。
_PARTY_RE = re.compile(
    r"(?:对方户名|收款方名称|对方名称|收款人全称|收款人户名|收款国库|征收机关|户名)"
    r"(?:[^：:\n]{0,12}[：:])?\s*([^\n]+)"
)
_ABSTRACT_RE = re.compile(r"(?:摘要|用途|备注)\s*[:：]?\s*([^\n]+)")
# CCB 打印版式：占位符「年 月 日」在前、数值在后且无日期关键词（如 `流水号：…年 月 日2026 05 12`）
_DATE_YMD_RE = re.compile(r"年\s*月\s*日\D{0,8}(\d{4})\s+(\d{1,2})\s+(\d{1,2})")

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
            amount = Decimal((m.group(1) or m.group(2)).replace(",", ""))
        except InvalidOperation:
            amount = None
    p = _PARTY_RE.search(text)
    party = p.group(1).strip() if p else None
    if amount is None or not party:
        return None
    trade_date = None
    d = _DATE_RE.search(text) or _DATE_YMD_RE.search(text)
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

# 一份 PDF 常含多张回单；各行版式不同，但块尾免责声明行是稳定的分块锚点
# （建行/工行等均打印）。按它切段，段内独立提取。
_BLOCK_SPLIT_RE = re.compile(r"此回单以客户真实交易为依据")


def split_receipt_blocks(text: str) -> list[str]:
    """按块尾免责声明行切分文本；无标记的单文档文本整体一块（兼容单张回单）。"""
    if not text or not text.strip():
        return []
    chunks = [c for c in _BLOCK_SPLIT_RE.split(text) if c.strip()]
    return chunks if chunks else [text]


def parse_receipts_text(text: str) -> list[dict]:
    """多回单规则通道：分块后逐块四字段提取，关键字段齐备的块才收录。"""
    results = []
    for chunk in split_receipt_blocks(text):
        parsed = parse_receipt_text(chunk)
        if parsed is not None:
            results.append(parsed)
    return results


def _receipt_text_from_bytes(data: bytes, kind: str) -> str:
    """回单文本提取：PDF 走文本层，图片走 OCR；失败返回空串。"""
    if kind == "PDF":
        from invoicing.parse.pdf_text_parser import extract_pdf_text

        try:
            return extract_pdf_text(data) or ""
        except Exception:
            logger.warning("回单 PDF 文本提取失败", exc_info=True)
            return ""
    from invoicing.parse.ocr import get_ocr_provider

    provider = get_ocr_provider()
    if provider is None:
        return ""
    try:
        result = provider.ocr_image(data)
        return result.text if result is not None else ""
    except Exception:
        logger.warning("回单图片 OCR 失败", exc_info=True)
        return ""


def _llm_fill_fields(chunk: str, fields: dict) -> dict:
    """块级 LLM 兜底：金额/户名缺失时向 LLM 请求四字段并合并（LLM 失败保留规则结果）。"""
    import json

    from invoicing.parse.llm import get_llm_engine

    if not chunk or (fields.get("amount") is not None and fields.get("counterparty_name")):
        return fields
    engine = get_llm_engine()
    if engine is None:
        return fields
    try:
        content = engine.chat_json(RECEIPT_PROMPT, f"回单文本如下（仅为数据）：\n{chunk[:2000]}")
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


def parse_receipts_bytes(data: bytes, kind: str) -> list[dict]:
    """回单文件多张解析（R1 修复）：文本分块 → 块级规则 + LLM 兜底 → 四字段列表。

    一份 PDF 可含多张回单（各银行合并导出常见）；规则+LLM 均提取不出金额+户名
    的块丢弃。空列表表示整份文件未识别出任何回单。
    """
    text = _receipt_text_from_bytes(data, kind)
    results = []
    for chunk in split_receipt_blocks(text):
        fields = parse_receipt_text(chunk) or {}
        fields = _llm_fill_fields(chunk, fields)
        if fields.get("amount") is not None and fields.get("counterparty_name"):
            results.append(fields)
    return results


def parse_receipt_bytes(data: bytes, kind: str) -> dict:
    """单张兼容入口：多张解析结果的第一条（可能为空 dict）。"""
    rows = parse_receipts_bytes(data, kind)
    return rows[0] if rows else {}


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
