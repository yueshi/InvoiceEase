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


def test_layout_reconstruction_merges_char_per_line_text():
    """P1 版面感知：pypdf 逐字换行（付\n款\n人\n全称 贾琨）实为水平标签，
    按坐标分桶还原为同一逻辑行（真实版式：标签 x 递增、y 相同）。"""
    from invoicing.parse.receipt_layout import reconstruct_lines

    chars: list[tuple[str, float, float]] = []
    for i, ch in enumerate("付款人全称"):
        chars.append((ch, 26.0 + i * 10, 730.7))  # 水平：x 递增、y 相同
    for i, ch in enumerate("贾琨"):
        chars.append((ch, 80.0 + i * 10, 730.7))
    for i, ch in enumerate("金额 1,600.00"):
        chars.append((ch, 200.0 + i * 9, 710.0))  # 另一行（x 起点不同，避免与上行首字同列）

    lines = reconstruct_lines(chars)
    assert any("付款人全称" in l and "贾琨" in l for l in lines), lines
    assert any("1,600.00" in l for l in lines)
    assert len(lines) == 2  # 同一 y 的字符必须归并到一行


def test_layout_reconstruction_keeps_distinct_lines():
    """不同 y 的行不得被并成一行（y 容差 3pt）。"""
    from invoicing.parse.receipt_layout import reconstruct_lines

    chars = [(c, 26.0 + i * 9, y) for y, s in ((700.0, "第一行"), (600.0, "第二行"))
             for i, c in enumerate(s)]
    lines = reconstruct_lines(chars)
    assert lines == ["第一行", "第二行"]


def test_field_validation_flags_suspicious_values():
    """P1 字段级校验：日期越界/账号形态异常/金额非正 → 质量标记。"""
    from invoicing.parse.receipt import extract_abstract, parse_receipt_text  # noqa: F401

    from invoicing.parse.receipt_layout import validate_fields

    issues = validate_fields(
        {"amount": Decimal("-5.00"), "trade_date": date(1980, 1, 1), "counterparty_name": "12345678"},
    )
    assert "amount_not_positive" in issues
    assert "date_out_of_range" in issues
    assert "party_looks_like_account" in issues

    ok = validate_fields(
        {"amount": Decimal("100.00"), "trade_date": date(2026, 1, 1), "counterparty_name": "某某公司"},
    )
    assert ok == []


def test_block_split_uses_template_markers():
    """分块按模板标记：工行语料含「回单仅供查询」块尾 → 可切出独立块。"""
    text = _load("icbc_transfer_chunk.txt") + _load("icbc_transfer_chunk.txt")
    tpl = get_template("icbc")
    heads, ends = block_markers(tpl)
    blocks = split_receipt_blocks(text, head_markers=heads, end_markers=ends)
    assert len(blocks) >= 2  # 两个块（而非整段一块）
