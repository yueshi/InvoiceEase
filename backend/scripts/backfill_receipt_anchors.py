"""回填/重算回单原件定位（page_no/anchor）。

背景：无锚点（R1.2 之前入库）或锚点算法版本落后（如 v1 的中点切带存在偏移，
已由 v2 结构边界取代）时，按 file_url 分组重解析原件，只更新定位列，
不动业务字段；匹配用「金额+交易日期」，异常时退化为顺序匹配（数量一致才启用）。

用法（在 backend 目录）：
    uv run python scripts/backfill_receipt_anchors.py [--dry-run]

幂等：处理 page_no IS NULL 或 anchor 版本 < 当前版本的行；可重复执行。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from invoicing.db import SessionLocal  # noqa: E402
from invoicing.models import BankReceipt  # noqa: E402
from invoicing.parse.receipt import (  # noqa: E402
    parse_receipts_bytes,
    self_account_set,
    self_name_set,
)
from invoicing.storage import get_storage  # noqa: E402


def sa_cast_version(anchor_col):
    """anchor JSON 里的算法版本（整数）；SQLite/PG 通用表达式。"""
    from sqlalchemy import Integer, cast, func

    return cast(func.json_extract(anchor_col, "$.v"), Integer)


def _match(targets: list[BankReceipt], parsed: list[dict]) -> list[tuple[BankReceipt, dict]]:
    """把待回填行与解析结果配对：优先（金额+日期）唯一匹配，否则数量一致时按序。"""
    pairs: list[tuple[BankReceipt, dict]] = []
    remaining = list(parsed)
    for row in targets:
        hit = None
        for p in remaining:
            if p.get("amount") == row.amount and p.get("trade_date") == row.trade_date:
                hit = p
                break
        if hit is not None:
            remaining.remove(hit)
            pairs.append((row, hit))
    if len(pairs) == len(targets):
        return pairs
    if len(targets) == len(parsed):
        return list(zip(targets, parsed))  # 数量一致 → 顺序对齐兜底
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    ap.add_argument(
        "--fill-abstracts",
        action="store_true",
        help="同时补充空的「摘要」（规则通道此前未覆盖缴款书/手续费/利息版式）",
    )
    args = ap.parse_args()

    from sqlalchemy import or_

    from invoicing.parse.receipt import ANCHOR_VERSION

    storage = get_storage()
    with SessionLocal() as db:
        targets = (
            db.query(BankReceipt)
            .filter(
                BankReceipt.file_type == "PDF",
                or_(
                    BankReceipt.page_no.is_(None),
                    BankReceipt.anchor.is_(None),
                    BankReceipt.bank_code.is_(None),  # 多银行 P0：银行识别补齐
                    # 版本落后（JSON 提取兼容 SQLite/PG）需重算
                    sa_cast_version(BankReceipt.anchor) < ANCHOR_VERSION,
                ),
            )
            .order_by(BankReceipt.file_url, BankReceipt.id)
            .all()
        )
        if not targets:
            print("无需回填（无 page_no 为空的 PDF 回单）")
            return
        accounts, names = self_account_set(db), self_name_set(db)
        by_file: dict[str, list[BankReceipt]] = {}
        for r in targets:
            by_file.setdefault(r.file_url, []).append(r)

        updated = 0
        for file_url, rows in by_file.items():
            try:
                data = storage.get(file_url)
            except Exception as e:
                print(f"  跳过（原件不可读）{file_url}: {e}")
                continue
            parsed = parse_receipts_bytes(
                data, "PDF", self_accounts=accounts, self_names=names
            )
            print(f"  {file_url.rsplit('/', 1)[-1]}: 待回填 {len(rows)} 条，重解析出 {len(parsed)} 条")
            for row, p in _match(rows, parsed):
                print(
                    f"    #{row.id} {row.amount} → P{p.get('page')} "
                    f"bbox={(p.get('anchor') or {}).get('bbox')}"
                )
                if not args.dry_run:
                    row.page_no = p.get("page")
                    row.anchor = p.get("anchor")
                    if not row.bank_code and p.get("bank_code"):
                        row.bank_code = p["bank_code"]  # 多银行 P0：银行识别补齐
                    if args.fill_abstracts and not row.abstract and p.get("abstract"):
                        row.abstract = p["abstract"]
                updated += 1
        if args.dry_run:
            print(f"[dry-run] 计划回填 {updated} 条")
            return
        db.commit()
        print(f"回填完成：{updated} 条")


if __name__ == "__main__":
    main()
