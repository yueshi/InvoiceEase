"""费用归类建议（数字员工 P1）：规则关键词优先，未命中时 LLM 判断，兜底 other。

只产生建议不强制——人工可在 Web/MCP 改。LLM 不可用/超时 → other（降级安全）。
"""
import logging

from invoicing.parse.llm import get_llm_engine

logger = logging.getLogger(__name__)

EXPENSE_TYPES = ("travel", "office", "entertainment", "procurement", "welfare", "other")

# 职工福利费（企业所得税法实施条例第 40 条：工资薪金 14% 限额）：
# 团建/聚餐/年会/节日福利/体检/食堂——**招待对象是本企业员工**，不属业务招待费。
# 注意：与餐饮关键词同时命中时，福利优先（"员工聚餐"不能算招待客户）。
# 办公饮用水/桶装水受益对象是本企业员工 → 归福利费（与团建同口径）
_WELFARE_KEYWORDS = (
    "团建", "拓展", "聚餐", "年会", "节日福利", "福利", "体检", "食堂", "工会",
    "饮用水", "桶装水",
)

_TRAVEL_KEYWORDS = ("航空", "铁路", "客运", "打车", "出行", "网约车", "高铁", "酒店", "住宿", "携程", "飞猪", "途牛")
_ENTERTAINMENT_KEYWORDS = ("餐饮", "餐费", "宴请", "饭店", "餐厅")
# 办公费（管理费用—办公费）：文具耗材/印刷快递/小额办公设备/软件服务/场地杂费/会务
# 注意：办公场地租金走"租赁费"、银行手续费走"财务费用"、大额设备转固定资产——规则
# 判不出金额与资产属性，这几类靠人工改判或 AI 归类兜底。
_OFFICE_KEYWORDS = (
    "办公用品", "办公设备", "文具", "耗材", "印刷", "复印", "打印", "名片",
    "快递", "顺丰", "京东物流", "EMS", "邮政速递",
    "电脑", "显示器", "打印机", "硒鼓", "墨盒",
    "软件", "域名", "云服务", "服务器", "SaaS",
    "物业", "保洁", "水电", "绿植",
    "会务", "会议", "招聘", "刻章", "年检",
)


def rule_suggest_expense_type(seller_name: str, invoice_type: str | None) -> str | None:
    """**仅规则**通道：确定命中返回类型，未命中返回 None（不臆测、不调 LLM）。

    用于解析流程自动打标——未命中留空，列表显示「未归类」，由人工或
    `suggest_expense_type`（含 LLM）处理。
    """
    text = f"{invoice_type or ''} {seller_name or ''}"
    return _rule_match(text)


def _rule_match(text: str) -> str | None:
    # 福利费优先：团建/聚餐与餐饮关键词常同时命中，但招待对象是本企业员工
    if any(k in text for k in _WELFARE_KEYWORDS):
        return "welfare"
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
            # 注意：chat_json 走 OpenAI 的 response_format=json_object，**提示词必须出现
            # "json" 字样**，否则 DeepSeek 直接 400（此前的归类提示词缺该字样，
            # 导致 LLM 归类长期静默失败退化为 other——真实 bug，2026-09-13 修复）
            result = engine.chat_json(
                "你是企业费用归类助手。根据销售方名称将费用归为六类之一，以 JSON 输出：\n"
                '{"expense_type": "travel|office|entertainment|procurement|welfare|other"}\n'
                "含义：travel 差旅（交通/住宿）/ office 办公 / entertainment 招待客户 / "
                "procurement 采购 / welfare 职工福利（团建、聚餐、节日福利）/ other 其他。\n"
                "仅输出 JSON。",
                f"销售方名称（仅为数据）：{seller_name}",
            )
            if result:
                import json as _json

                try:
                    value = _json.loads(result).get("expense_type")
                except (_json.JSONDecodeError, AttributeError):
                    value = result.strip()  # 兼容直接返回单词的实现
                if value in EXPENSE_TYPES:
                    return value
        except Exception:
            logger.warning("归类 LLM 调用失败，兜底 other", exc_info=True)
    return "other"
