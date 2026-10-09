"""P0-1 配置阈值测试（v1.1 §4.3 + §7.2 ✅5 阈值必配）。"""
from decimal import Decimal

from invoicing.config import settings


def test_large_amount_threshold_default():
    """大额阈值默认 5000 元（v1.1 §4.3 金额阈值节点）。"""
    assert settings.large_amount_threshold == Decimal("5000")


def test_over_threshold_tolerance_default():
    """超标容忍值默认 50 元。"""
    assert settings.over_threshold_tolerance == Decimal("50")


def test_tax_sum_tolerance_default():
    """价税合计容差 0.01（v1.1 §4.1 硬编码值通过 settings 暴露便于审计）。"""
    assert settings.tax_sum_tolerance == Decimal("0.01")


def test_amount_floor_default():
    """金额下限 0（必须 > 0）。"""
    assert settings.amount_floor == Decimal("0")