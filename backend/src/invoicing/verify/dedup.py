from sqlalchemy import func, select
from sqlalchemy.orm import Session

from invoicing.models import Invoice


def find_duplicate(db: Session, invoice: Invoice) -> Invoice | None:
    """按 (tenant_id, coalesce(invoice_code,''), invoice_number) 查重复。

    `invoice` 可以是库中对象（按 id 排除自身），也可以是未入库的探针对象
    （id 为 None 时不能加 id 过滤，否则 `id != NULL` 恒为假查不到任何行）。

    号码为空（None）不参与查重——否则 `invoice_number IS NULL` 会匹配任意
    空号记录（如解析失败的空壳票），造成跨票误判重复。
    """
    if not invoice.invoice_number:
        return None
    conds = [
        Invoice.tenant_id == invoice.tenant_id,
        func.coalesce(Invoice.invoice_code, "") == func.coalesce(invoice.invoice_code, ""),
        Invoice.invoice_number == invoice.invoice_number,
    ]
    if invoice.id is not None:
        conds.append(Invoice.id != invoice.id)
    stmt = select(Invoice).where(*conds)
    return db.scalars(stmt).first()
