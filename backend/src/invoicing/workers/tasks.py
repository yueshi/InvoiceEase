import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.models import Invoice, InvoiceStatus
from invoicing.parse.router import parse_file
from invoicing.storage import get_storage
from invoicing.workflow.state import transition
from invoicing.workers.queue import enqueue_verify_sync

logger = logging.getLogger(__name__)


def _apply_parsed_fields(inv: Invoice, parsed) -> None:
    inv.invoice_code = parsed.invoice_code
    inv.invoice_number = parsed.invoice_number
    inv.issue_date = parsed.issue_date
    inv.amount_without_tax = parsed.amount_without_tax
    inv.tax_amount = parsed.tax_amount
    inv.total_amount = parsed.total_amount
    inv.total_amount_cn = parsed.total_amount_cn
    inv.seller_name = parsed.seller_name
    inv.seller_tax_id = parsed.seller_tax_id
    inv.buyer_name = parsed.buyer_name
    inv.buyer_tax_id = parsed.buyer_tax_id
    inv.invoice_type = parsed.invoice_type
    inv.confidence_score = parsed.confidence_score
    inv.parse_source = parsed.parse_source


def _save_xml_original(storage, inv: Invoice, xml_data: bytes) -> None:
    """合规硬约束：无论原收到格式，XML 原件必须单独存档。"""
    if inv.file_type == "XML":
        inv.xml_url = inv.file_url  # 原件即 XML
    elif xml_data:
        xml_key = f"tenant-default/invoice-{inv.id}/original_invoice.xml"
        storage.put(xml_key, xml_data, "application/xml")
        inv.xml_url = xml_key


def _parse_invoice(invoice_id: int) -> None:
    parsed_code = parsed_number = None  # 查重探针值：解析成功后捕获，回滚后 inv 属性失效仍可用
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if inv is None or inv.status != InvoiceStatus.parsing.value:
            logger.info("跳过解析任务 invoice_id=%s status=%s", invoice_id, inv.status if inv else None)
            return
        storage = get_storage()
        data = storage.get(inv.file_url)
        outcome = parse_file(inv.file_type, data)

        if outcome.parsed is not None and not outcome.errors:
            parsed_code = outcome.parsed.invoice_code
            parsed_number = outcome.parsed.invoice_number
            _apply_parsed_fields(inv, outcome.parsed)
            _save_xml_original(storage, inv, outcome.xml_data)
            transition(inv, InvoiceStatus.parsed.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"source": outcome.source, "confidence": outcome.parsed.confidence_score},
            )
        else:
            # 结构化数据不可得 → 待复核（Phase 2 OCR 接入后此路径升级）
            inv.parse_source = outcome.source
            inv.confidence_score = 0.0
            inv.validation_errors = [e.model_dump() for e in outcome.errors]
            transition(inv, InvoiceStatus.pending_review.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"source": outcome.source, "errors": inv.validation_errors},
            )
        db.commit()
        if outcome.parsed is not None and not outcome.errors:
            enqueue_verify_sync(inv.id)
    except IntegrityError:
        # 解析出的「发票代码+号码」与库中已有发票冲突（唯一索引兜底并发）→ 查重拦截
        db.rollback()
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            raise
        from invoicing.verify.dedup import find_duplicate

        existing = find_duplicate(
            db,
            Invoice(
                tenant_id=inv.tenant_id,
                invoice_code=parsed_code,
                invoice_number=parsed_number,
            ),
        )
        if existing is not None:
            inv.duplicate_flag = True
            inv.duplicate_of_id = existing.id
            transition(inv, InvoiceStatus.blocked.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"result": "duplicate", "duplicate_of_id": existing.id},
            )
            db.commit()
        else:
            raise
    except Exception:
        db.rollback()
        logger.exception("解析任务异常 invoice_id=%s", invoice_id)
        raise
    finally:
        db.close()


async def parse_invoice_task(ctx, invoice_id: int) -> None:
    await asyncio.to_thread(_parse_invoice, invoice_id)


async def verify_invoice_task(ctx, invoice_id: int) -> None:
    # _verify_invoice 在 Task 11 中与本函数同文件定义，运行时解析
    await asyncio.to_thread(_verify_invoice, invoice_id)
