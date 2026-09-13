"""多银行回单模板库测试（表格驱动）：语料 = test/fixtures/receipts/<bank>_*.txt。

接入新银行的流程（本文件即验收）：加一份模板（bank_templates.py）+ 一段脱敏语料
+ 一行用例，**无需改解析代码**。
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoicing.parse.bank_templates import block_markers, detect_bank, get_template
from invoicing.parse.receipt import parse_receipt_text, split_receipt_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_detect_bank_by_keywords():
    assert detect_bank(_load("ccb_tax_chunk.txt")).code == "ccb"
    assert detect_bank(_load("icbc_transfer_chunk.txt")).code == "icbc"
    assert detect_bank("某不知名银行的凭证") is None  # 未命中 → 走通用兜底


def test_block_markers_fall_back_to_generic():
    heads, ends = block_markers(get_template("icbc"))
    assert "电子回单" in heads or "业务回单" in heads
    assert ends  # 非空
    generic_heads, generic_ends = block_markers(None)
    assert generic_heads and generic_ends  # 无模板也有通用兜底


# 表格驱动：<语料, 期望字段>
CASES = [
    pytest.param(
        "ccb_tax_chunk.txt",
        {
            "bank": "ccb",
            "amount": Decimal("1116.00"),
            "party_any_of": ("国家金库", "税务局"),
            "trade_date": date(2026, 4, 20),
            "abstract": "企业职工基本养老保险费",
            "direction": "付",
        },
        id="ccb-tax",
    ),
    pytest.param(
        "icbc_transfer_chunk.txt",
        {
            "bank": "icbc",
            "amount": Decimal("12345.67"),
            "party_any_of": ("北京某某科技",),
            "trade_date": date(2026, 8, 15),
            "abstract": "货款",
            "direction": None,  # 工行转账模板未定义方向规则 → 不臆测
        },
        id="icbc-transfer",
    ),
]


@pytest.mark.parametrize("fixture,expected", CASES)
def test_bank_receipt_parses_by_template(fixture, expected):
    """模板驱动解析：字段全部来自该行模板的标签表（无 LLM 参与）。"""
    text = _load(fixture)
    tpl = detect_bank(text)
    assert tpl is not None and tpl.code == expected["bank"]

    parsed = parse_receipt_text(text, template=tpl)
    assert parsed is not None
    assert parsed["amount"] == expected["amount"]
    party = parsed["counterparty_name"] or ""
    assert any(k in party for k in expected["party_any_of"])
    assert "西安启智" not in party  # 本司名称不得被当作对方
    assert parsed["trade_date"] == expected["trade_date"]
    assert parsed["abstract"] == expected["abstract"]
    assert parsed.get("direction") == expected["direction"]


def test_block_split_uses_template_markers():
    """分块按模板标记：工行语料含「回单仅供查询」块尾 → 可切出独立块。"""
    text = _load("icbc_transfer_chunk.txt") + _load("icbc_transfer_chunk.txt")
    tpl = get_template("icbc")
    heads, ends = block_markers(tpl)
    blocks = split_receipt_blocks(text, head_markers=heads, end_markers=ends)
    assert len(blocks) >= 2  # 两个块（而非整段一块）
