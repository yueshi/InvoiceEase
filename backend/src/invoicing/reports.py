"""成本报表（数字员工 P1）：月度聚合（双金额口径）+ Excel 台账导出（金蝶/用友兼容列）。

口径说明：total_amount=价税合计（小规模纳税人成本口径）；total_without_tax=不含税
（一般纳税人成本口径）；by_type/by_center 按价税合计分布，明细行三金额齐备。
"""
import io
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from invoicing.models import Invoice

VALID_STATUS = ("parsed", "pending_review", "verifying", "pending_submit", "submitted", "archived")


def _month_bounds(month: str) -> tuple[date, date]:
    try:
        y, m = month.split("-")
        year, mon = int(y), int(m)
        if not 1 <= mon <= 12:
            raise ValueError
    except ValueError:
        raise ValueError(f"非法月份: {month}（格式 YYYY-MM，月份 01-12）") from None
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end


def _quarter_bounds(quarter: str) -> tuple[date, date]:
    """季度边界：`YYYY-QN`（N=1-4）→ [首日, 次季首日)。"""
    import re as _re

    m = _re.match(r"^(\d{4})-Q([1-4])$", quarter or "")
    if not m:
        raise ValueError(f"非法季度: {quarter}（格式 YYYY-QN，N=1-4）")
    year, q = int(m.group(1)), int(m.group(2))
    start_mon = (q - 1) * 3 + 1
    start = date(year, start_mon, 1)
    end = date(year + 1, 1, 1) if q == 4 else date(year, start_mon + 3, 1)
    return start, end


def period_bounds(month: str | None = None, quarter: str | None = None) -> tuple[date, date]:
    """周期边界解析：month 与 quarter 恰给其一（互斥），否则 ValueError。"""
    if bool(month) == bool(quarter):
        raise ValueError("month 与 quarter 必须且只能提供一个")
    return _quarter_bounds(quarter) if quarter else _month_bounds(month)


def receipts_in_range(db: Session, start: date, end: date) -> list:
    """区间回单清单（R1 修复：缺日期回单不得隐身）。

    trade_date 落区间优先；trade_date 为 NULL（规则/LLM 均未提取出日期）时按
    created_at 归区间。此前直接按 trade_date 过滤，SQL 中 NULL 比较恒假，
    缺日期回单在任何视图都不可见。
    """
    from invoicing.models import BankReceipt

    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.min)
    return (
        db.query(BankReceipt)
        .filter(
            or_(
                and_(BankReceipt.trade_date >= start, BankReceipt.trade_date < end),
                and_(
                    BankReceipt.trade_date.is_(None),
                    BankReceipt.created_at >= start_dt,
                    BankReceipt.created_at < end_dt,
                ),
            )
        )
        .order_by(BankReceipt.trade_date, BankReceipt.id)
        .all()
    )


def receipts_in_month(db: Session, month: str) -> list:
    """当月回单（兼容入口，MCP receipt_list 等沿用）。"""
    return receipts_in_range(db, *_month_bounds(month))


def receipts_in_period(db: Session, month: str | None = None, quarter: str | None = None) -> list:
    """按月或按季度取回单（month/quarter 恰给其一）。"""
    return receipts_in_range(db, *period_bounds(month, quarter))


def _month_rows(db: Session, month: str, tenant_id: str) -> list[Invoice]:
    start, end = _month_bounds(month)
    return (
        db.query(Invoice)
        .filter(
            Invoice.tenant_id == tenant_id,
            Invoice.issue_date >= start,
            Invoice.issue_date < end,
            Invoice.status.in_(VALID_STATUS),
        )
        .order_by(Invoice.issue_date, Invoice.id)
        .all()
    )


def monthly_cost(db: Session, month: str, tenant_id: str = "default") -> dict:
    """月度成本聚合（双金额口径，类型/部门分布；tenant 过滤恒开）。"""
    rows = _month_rows(db, month, tenant_id)
    total = sum((r.total_amount or Decimal("0")) for r in rows)
    total_wo = sum((r.amount_without_tax or Decimal("0")) for r in rows)
    total_tax = sum((r.tax_amount or Decimal("0")) for r in rows)
    by_type: dict[str, Decimal] = {}
    by_center: dict[str, Decimal] = {}
    for r in rows:
        key_t = r.expense_type or "unclassified"
        key_c = r.cost_center or "未归属"
        by_type[key_t] = by_type.get(key_t, Decimal("0")) + (r.total_amount or Decimal("0"))
        by_center[key_c] = by_center.get(key_c, Decimal("0")) + (r.total_amount or Decimal("0"))
    return {
        "month": month,
        "tenant_id": tenant_id,
        "total_count": len(rows),
        "total_amount": total,
        "total_without_tax": total_wo,
        "total_tax": total_tax,
        "by_type": by_type,
        "by_center": by_center,
        "rows": [
            {
                "id": r.id,
                "invoice_number": r.invoice_number,
                "issue_date": str(r.issue_date) if r.issue_date else None,
                "seller_name": r.seller_name,
                "amount_without_tax": str(r.amount_without_tax) if r.amount_without_tax else None,
                "tax_amount": str(r.tax_amount) if r.tax_amount else None,
                "total_amount": str(r.total_amount) if r.total_amount else None,
                "expense_type": r.expense_type,
                "cost_center": r.cost_center,
                "status": r.status,
            }
            for r in rows
        ],
    }


def monthly_health(db: Session, month: str) -> str:
    """月度健康报告（P3/R3）：老板视角收口文本（收票/验真/异常/成本/无票/信任）。"""
    from invoicing.models import AuditLog, BankReceipt

    cost = monthly_cost(db, month)
    start, end = _month_bounds(month)
    rows = (
        db.query(Invoice)
        .filter(Invoice.issue_date >= start, Invoice.issue_date < end)
        .all()
    )
    by_source: dict[str, int] = {}
    verify_failed = 0
    blocked = 0
    red = 0
    for inv in rows:
        key = inv.parse_source or "unknown"
        by_source[key] = by_source.get(key, 0) + 1
        if inv.verify_status == "failed":
            verify_failed += 1
        if inv.status == "blocked":
            blocked += 1
        if inv.red_flag:
            red += 1
    receipts = (
        db.query(BankReceipt)
        .filter(BankReceipt.trade_date >= start, BankReceipt.trade_date < end)
        .all()
    )
    unmatched = [r for r in receipts if r.paired_invoice_id is None]
    unmatched_total = sum((r.amount for r in unmatched if r.amount), 0)
    # 信任（M8）：近 7 天改判统计（跨月滚动窗口，观察期数据）
    from datetime import timedelta

    from invoicing.models.fields import utcnow

    since = utcnow() - timedelta(days=7)
    trust_logs = (
        db.query(AuditLog)
        .filter(AuditLog.action.in_(("REVIEW", "AUTO_REVIEW")), AuditLog.created_at >= since)
        .all()
    )
    auto_count = sum(1 for l in trust_logs if l.action == "AUTO_REVIEW")
    directional = [
        l for l in trust_logs
        if l.action == "REVIEW" and (l.detail or {}).get("ai_verdict") in ("approve", "reject")
    ]
    overturn = sum(
        1 for l in directional
        if (l.detail or {}).get("ai_verdict") != (l.detail or {}).get("action")
    )
    overturn_rate = overturn / len(directional) if directional else 0.0

    type_lines = "；".join(f"{k} {v}元" for k, v in cost["by_type"].items()) or "（无归类）"
    return "\n".join([
        f"📊 {month} 月度健康报告",
        f"收票：共 {len(rows)} 张（" + " / ".join(f"{k} {v}" for k, v in sorted(by_source.items())) + "）",
        f"验真：失败 {verify_failed} 张（当前为模拟模式，未接入国税查验平台）",
        f"异常：拦截 {blocked}、红字 {red}",
        f"成本：合计 {cost['total_amount']} 元（不含税 {cost['total_without_tax']} + 税额 {cost['total_tax']}）",
        f"费用构成：{type_lines}",
        f"无票支出：{len(unmatched)} 笔（合计 {unmatched_total} 元，建议催交发票）",
        f"数字员工：近 7 天自动处理 {auto_count} 张，人工改判 {overturn} 张（改判率 {overturn_rate * 100:.0f}%）",
    ])


def receipts_to_csv(db: Session, month: str | None = None, quarter: str | None = None) -> bytes:
    """银行回单凭证草稿 CSV（P3/R1）：金蝶/用友凭证导入通用列。

    借方=费用类（摘要），贷方=银行存款；已配对行附发票号。
    """
    import csv
    import io as _io

    from invoicing.models import BankReceipt

    rows = receipts_in_period(db, month=month, quarter=quarter)
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["日期", "摘要", "对方户名", "金额", "借方", "贷方", "发票号"])
    for r in rows:
        # P2：借贷方向随收付方向——收（利息/收款回单）=借银行/贷收入；付/未定=借费用/贷银行
        if r.direction == "收":
            debit, credit = "银行存款", (r.abstract or "收入")
        else:
            debit, credit = (r.abstract or "费用"), "银行存款"
        writer.writerow([
            str(r.trade_date) if r.trade_date else "",
            r.abstract or "",
            r.counterparty_name or "",
            str(r.amount) if r.amount else "",
            debit,
            credit,
            str(r.paired_invoice_id) if r.paired_invoice_id else "",
        ])
    return buf.getvalue().encode("utf-8-sig")  # BOM：Excel 打开中文不乱码


def export_monthly_excel(db: Session, month: str, tenant_id: str = "default") -> bytes:
    """Excel 台账（三 sheet）：明细（含三金额列，金蝶/用友常见列）/按类型/按部门。"""
    from openpyxl import Workbook

    data = monthly_cost(db, month, tenant_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "发票明细"
    headers = ["开票日期", "发票号码", "销售方", "不含税金额", "税额", "价税合计", "费用类型", "部门/项目", "状态"]
    ws.append(headers)
    for r in data["rows"]:
        ws.append([
            r["issue_date"], r["invoice_number"], r["seller_name"],
            r["amount_without_tax"], r["tax_amount"], r["total_amount"],
            r["expense_type"] or "", r["cost_center"] or "", r["status"],
        ])
    ws2 = wb.create_sheet("按费用类型")
    ws2.append(["费用类型", "价税合计"])
    for k, v in data["by_type"].items():
        ws2.append([k, str(v)])
    ws3 = wb.create_sheet("按部门项目")
    ws3.append(["部门/项目", "价税合计"])
    for k, v in data["by_center"].items():
        ws3.append([k, str(v)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
