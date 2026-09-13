"""版面工具与字段级校验（多银行 P1）。

**已接入**：validate_fields —— 解析产出后统一校验（日期越界/金额非正/户名像账号），
转质量标记与 needs_review。

**实验性（未接入主链路）**：reconstruct_lines 坐标法版面还原。
实测结论：建行回单标签既有全横排（付 x=26→称 x=66 同 y），也有「竖排+横排混合」
（付/款/人 竖排在 x=29，全称 贾琨 横排于其右侧）；同一页还混排多张回单与页面
装饰字符，纯规则化的整页重排会互相污染（多轮尝试未达可靠）。当前拦不住的字段
继续由 LLM 兜底；后续应改为「局部版面查询」（在标签附近小范围内用坐标取值，
只对规则失败的字段生效），而不是整页重排。
"""
import re
from datetime import date
from decimal import Decimal

# ---- 版面还原（坐标法）----------------------------------------------------

_Y_TOLERANCE = 3.0  # 同一文本行的 y 容差（点）


_Y_TOLERANCE = 3.0  # 同一文本行的 y 容差（点）
_COL_GAP_MIN, _COL_GAP_MAX = 5.0, 40.0  # 竖排列内字间距合理区间（点）


def _bucket_lines(chars: list[tuple[str, float, float]]) -> list[dict]:
    """把 (字符, x, y) 按 y 分桶成行（y 容差内归一），行内按 x 排序，自上而下。"""
    lines: list[dict] = []
    for ch, x, y in chars:
        if ch.strip() == "":
            continue
        target = None
        for line in lines:
            if abs(line["y"] - y) <= _Y_TOLERANCE:
                target = line
                break
        if target is None:
            target = {"y": y, "items": []}
            lines.append(target)
        target["items"].append((x, ch))
    for line in lines:
        line["items"].sort(key=lambda it: it[0])
    lines.sort(key=lambda l: -l["y"])
    return lines


def reconstruct_lines(chars: list[tuple[str, float, float]]) -> list[str]:
    """坐标分桶：把 (字符, x, y) 还原为自上而下的逻辑行（行内按 x 排序）。

    这是版面处理**可靠的原语**（实测：pypdf 逐字换行造成的"伪竖排"，
    全横排的标签用分桶即可还原）。**不做**跨行合并——竖排/横排混合版式
    （付/款/人 竖排 + 全称 贾琨 横排）的多轮启发式尝试均不可靠，
    详见模块 docstring 的结论与后续方向。
    """
    return ["".join(ch for _, ch in line["items"]) for line in _bucket_lines(chars)]


# ---- 字段级校验 -----------------------------------------------------------

_MIN_DATE = date(1990, 1, 1)


def validate_fields(fields: dict, today: date | None = None) -> list[str]:
    """字段级校验 → 质量问题列表（空 = 通过）。

    - amount_not_positive：金额缺失/非正
    - date_out_of_range：日期早于 1990 或晚于「今天 + 1 年」
    - party_looks_like_account：对方户名是纯数字/账号形态（多半取错字段）
    """
    issues: list[str] = []
    amount = fields.get("amount")
    if amount is None or (isinstance(amount, Decimal) and amount <= 0):
        issues.append("amount_not_positive")

    trade_date = fields.get("trade_date")
    if trade_date is not None:
        today = today or date.today()
        try:
            upper = date(today.year + 1, today.month, today.day)
        except ValueError:  # 2/29
            upper = date(today.year + 1, today.month, 28)
        if trade_date < _MIN_DATE or trade_date > upper:
            issues.append("date_out_of_range")

    party = (fields.get("counterparty_name") or "").strip()
    if party and re.fullmatch(r"[\d\s\-]{6,}", party):
        issues.append("party_looks_like_account")
    return issues
