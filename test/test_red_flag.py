"""红字发票识别测试（M9 第一步：识别+标记，不自动对冲）。"""
from invoicing.parse.red_flag import detect_red_invoice


def test_detect_by_text_keywords():
    assert detect_red_invoice("红字发票 电子普通发票", None) is True
    assert detect_red_invoice("此为红冲发票", None) is True
    assert detect_red_invoice("负数发票", None) is True


def test_detect_by_invoice_type():
    assert detect_red_invoice(None, "红字发票") is True
    assert detect_red_invoice(None, "增值税普通发票（红字）") is True


def test_normal_invoice_not_flagged():
    assert detect_red_invoice("电子发票（普通发票）", "增值税电子普通发票") is False
    assert detect_red_invoice(None, None) is False
    assert detect_red_invoice("", "") is False
