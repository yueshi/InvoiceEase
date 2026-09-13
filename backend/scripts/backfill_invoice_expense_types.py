"""回填发票费用类型（自动类型标签）：规则优先，未命中留空（= 未归类）。

背景：自动打标上线前入库的发票 expense_type 为空；本脚本按 `rule_suggest_expense_type`
（纯规则、不调 LLM）回填，未命中的保持空——由人工或 AI 归类处理。

用法（backend 目录）：
    uv run python scripts/backfill_invoice_expense_types.py [--dry-run] [--use-llm]

--use-llm：规则未命中时调用 LLM 建议（较慢，按需使用）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from invoicing.db import SessionLocal  # noqa: E402
from invoicing.models import Invoice  # noqa: E402
from invoicing.parse.classify import rule_suggest_expense_type, suggest_expense_type  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--use-llm", action="store_true", help="规则未命中时调 LLM 建议")
    args = ap.parse_args()

    with SessionLocal() as db:
        rows = db.query(Invoice).filter(Invoice.expense_type.is_(None)).all()
        print(f"待回填（未归类）：{len(rows)} 张")
        filled = llm_used = 0
        for inv in rows:
            hit = rule_suggest_expense_type(inv.seller_name, inv.invoice_type)
            source = "规则"
            if hit is None and args.use_llm:
                hit = suggest_expense_type(inv.seller_name, inv.invoice_type)
                source = "LLM"
                llm_used += 1
            if hit in (None, "other") and source == "LLM":
                print(f"  #{inv.id} {(inv.seller_name or '')[:24]} → LLM 也给不出确定类型，留空")
                continue
            if hit is None:
                print(f"  #{inv.id} {(inv.seller_name or '')[:24]} → 未命中，留空（未归类）")
                continue
            print(f"  #{inv.id} {(inv.seller_name or '')[:24]} → {hit}（{source}）")
            if not args.dry_run:
                inv.expense_type = hit
            filled += 1
        if not args.dry_run:
            db.commit()
        print(f"完成：回填 {filled} 张（其中 LLM 建议 {llm_used} 张）")


if __name__ == "__main__":
    main()
