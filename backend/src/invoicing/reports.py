"""成本报表（数字员工 P1）：月度聚合（双金额口径）+ Excel 台账导出（金蝶/用友兼容列）。

口径说明：total_amount=价税合计（小规模纳税人成本口径）；total_without_tax=不含税
（一般纳税人成本口径）；by_type/by_center 按价税合计分布，明细行三金额齐备。
"""
import io
from datetime import date
from decimal import Decimal

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


def receipts_to_csv(db: Session, month: str) -> bytes:
    """银行回单凭证草稿 CSV（P3/R1）：金蝶/用友凭证导入通用列。

    借方=费用类（摘要），贷方=银行存款；已配对行附发票号。
    """
    import csv
    import io as _io

    from invoicing.models import BankReceipt

    start, end = _month_bounds(month)
    rows = (
        db.query(BankReceipt)
        .filter(BankReceipt.trade_date >= start, BankReceipt.trade_date < end)
        .order_by(BankReceipt.trade_date, BankReceipt.id)
        .all()
    )
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["日期", "摘要", "对方户名", "金额", "借方", "贷方", "发票号"])
    for r in rows:
        writer.writerow([
            str(r.trade_date) if r.trade_date else "",
            r.abstract or "",
            r.counterparty_name or "",
            str(r.amount) if r.amount else "",
            r.abstract or "费用",
            "银行存款",
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
