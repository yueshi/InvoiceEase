"""红字发票识别（数字员工 P2/M9 第一步）：识别+标记，不自动对冲。

宁漏勿错：仅票面文本/发票类型含明确红字特征词才标记；对冲规则待财务确认
后另立任务。识别来源：解析产出的发票文本（PDF_TEXT/OCR 通道）与 invoice_type。
"""
_RED_KEYWORDS = ("红字发票", "红冲", "负数发票", "（红字）", "(红字)")


def detect_red_invoice(text: str | None, invoice_type: str | None) -> bool:
    """票面文本或发票类型含红字特征词 → True；宁漏勿错。"""
    haystack = f"{text or ''} {invoice_type or ''}"
    return any(k in haystack for k in _RED_KEYWORDS)
