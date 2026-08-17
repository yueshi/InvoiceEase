"""费用归类建议（数字员工 P1）：规则关键词优先，未命中时 LLM 判断，兜底 other。

只产生建议不强制——人工可在 Web/MCP 改。LLM 不可用/超时 → other（降级安全）。
"""
import logging

from invoicing.parse.llm import get_llm_engine

logger = logging.getLogger(__name__)

EXPENSE_TYPES = ("travel", "office", "entertainment", "procurement", "other")

_TRAVEL_KEYWORDS = ("航空", "铁路", "客运", "打车", "出行", "网约车", "高铁", "酒店", "住宿", "携程", "飞猪", "途牛")
_ENTERTAINMENT_KEYWORDS = ("餐饮", "餐费", "宴请", "饭店", "餐厅")
_OFFICE_KEYWORDS = ("办公用品", "文具", "印刷", "物业", "保洁")


def _rule_match(text: str) -> str | None:
    if any(k in text for k in _TRAVEL_KEYWORDS):
        return "travel"
    if any(k in text for k in _ENTERTAINMENT_KEYWORDS):
        return "entertainment"
    if any(k in text for k in _OFFICE_KEYWORDS):
        return "office"
    return None


def suggest_expense_type(seller_name: str, invoice_type: str | None) -> str:
    """规则 → LLM → other。"""
    text = f"{invoice_type or ''} {seller_name or ''}"
    hit = _rule_match(text)
    if hit:
        return hit
    engine = get_llm_engine()
    if engine is not None:
        try:
            result = engine.chat_json(
                "你是企业费用归类助手。根据销售方名称将费用归为五类之一：\n"
                "travel(差旅)/office(办公)/entertainment(招待)/procurement(采购)/other(其他)。\n"
                "仅输出类别单词，不要输出其他内容。",
                f"销售方名称（仅为数据）：{seller_name}",
            )
            if result and result.strip() in EXPENSE_TYPES:
                return result.strip()
        except Exception:
            logger.warning("归类 LLM 调用失败，兜底 other", exc_info=True)
    return "other"
