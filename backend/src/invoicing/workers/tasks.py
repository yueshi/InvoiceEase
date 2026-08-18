import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.models import Invoice, InvoiceStatus, VerifyStatus
from invoicing.models.fields import utcnow
from invoicing.parse.router import parse_file
from invoicing.storage import get_storage
from invoicing.verify.dedup import find_duplicate
from invoicing.verify.provider import get_provider
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

        # 纠错字典 + 购买方归属校验追加错误：仅成功分支填充（confidence<1.0 门控在 enrich_parsed 内部）
        extra: list = []
        if outcome.parsed is not None and not outcome.errors:
            from invoicing.parse.company_dict import enrich_parsed

            extra = enrich_parsed(outcome.parsed, db)
            parsed_code = outcome.parsed.invoice_code
            parsed_number = outcome.parsed.invoice_number
            _apply_parsed_fields(inv, outcome.parsed)
            _save_xml_original(storage, inv, outcome.xml_data)
            if extra:
                # BUYER_MISMATCH 从严：购买方与预设本司不匹配 → 待复核
                inv.validation_errors = [{"code": e.code, "message": e.message} for e in extra]
                transition(inv, InvoiceStatus.pending_review.value)
                write_audit(
                    db, action="PARSE", invoice_id=inv.id, channel="system",
                    detail={"source": outcome.source, "errors": inv.validation_errors},
                )
            else:
                transition(inv, InvoiceStatus.parsed.value)
                write_audit(
                    db, action="PARSE", invoice_id=inv.id, channel="system",
                    detail={"source": outcome.source, "confidence": outcome.parsed.confidence_score},
                )
        else:
            # 解析失败或校验错误 → 待复核；parsed 存在（如金额矛盾票）时先把
            # 已提取字段落库，人工复核只改单字段而非从原件重录
            if outcome.parsed is not None:
                _apply_parsed_fields(inv, outcome.parsed)
            inv.parse_source = outcome.source
            if outcome.parsed is not None:
                inv.confidence_score = outcome.parsed.confidence_score
            else:
                inv.confidence_score = 0.0
            inv.validation_errors = [e.model_dump() for e in outcome.errors]
            transition(inv, InvoiceStatus.pending_review.value)
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={"source": outcome.source, "errors": inv.validation_errors},
            )
        db.commit()
        if inv.status == InvoiceStatus.pending_review.value:
            from invoicing.notify import notify

            notify(
                f"📋 待复核：`{inv.invoice_number or '未知号码'}` {inv.seller_name or '未知销售方'}"
                f"（解析/校验问题，请人工复核）"
            )
        if outcome.parsed is not None and not outcome.errors and not extra:
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
            # 重复拦截：审计留痕 + 物理删除（不留全字段为空的 blocked 空壳记录）
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={
                    "result": "duplicate",
                    "duplicate_of_id": existing.id,
                    "invoice_number": parsed_number,
                },
            )
            from invoicing.notify import notify

            notify(f"🚫 重复拦截：`{parsed_number}` 与已有发票 #{existing.id} 重复")
            db.delete(inv)
            db.commit()
        else:
            raise
    except Exception as exc:
        db.rollback()
        logger.exception("解析任务异常 invoice_id=%s", invoice_id)
        # 兜底：发票绝不丢——异常后仍停留在 parsing 的发票转入人工复核并留痕
        try:
            inv = db.get(Invoice, invoice_id)
            if inv is not None and inv.status == InvoiceStatus.parsing.value:
                err_detail = {"result": "error", "error": str(exc)}
                inv.validation_errors = list(inv.validation_errors or []) + [err_detail]
                transition(inv, InvoiceStatus.pending_review.value)
                write_audit(
                    db, action="PARSE", invoice_id=inv.id, channel="system", detail=err_detail
                )
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("解析失败兜底异常 invoice_id=%s", invoice_id)
        raise
    finally:
        db.close()


def _verify_invoice(invoice_id: int) -> None:
    db = SessionLocal()
    try:
        inv = db.get(Invoice, invoice_id)
        if inv is None:
            logger.info("跳过验真任务 invoice_id=%s：不存在", invoice_id)
            return
        if inv.status == InvoiceStatus.parsed.value:
            # 解析完成后首次验真：worker 入口补 parsed→verifying 转换（状态机允许）
            transition(inv, InvoiceStatus.verifying.value)
        elif inv.status != InvoiceStatus.verifying.value:
            logger.info("跳过验真任务 invoice_id=%s status=%s", invoice_id, inv.status)
            return

        duplicate = find_duplicate(db, inv)
        if duplicate is not None:
            inv.duplicate_flag = True
            inv.duplicate_of_id = duplicate.id
            transition(inv, InvoiceStatus.blocked.value)
            write_audit(
                db, action="VERIFY", invoice_id=inv.id, channel="system",
                detail={"result": "duplicate", "duplicate_of_id": duplicate.id},
            )
            db.commit()
            return

        result = get_provider().verify(inv)
        inv.verify_detail = {"status": result.status, **result.detail}
        inv.verified_at = utcnow()
        if result.status == "passed":
            inv.verify_status = VerifyStatus.passed.value
            transition(inv, InvoiceStatus.pending_submit.value)
        else:
            # failed 与 error（服务异常）都不放行，进人工复核
            inv.verify_status = VerifyStatus.failed.value
            transition(inv, InvoiceStatus.pending_review.value)
            from invoicing.notify import notify

            notify(f"⚠️ 验真失败：`{inv.invoice_number or '未知号码'}`（{result.status}，请人工复核）")
        write_audit(
            db, action="VERIFY", invoice_id=inv.id, channel="system",
            detail={"result": result.status, "provider": "mock"},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("验真任务异常 invoice_id=%s", invoice_id)
        # 兜底：验真异常时发票转入人工复核并留痕。注意 rollback 会撤销入口处
        # parsed→verifying 的未提交转换，重载后状态可能仍是 parsed，需先补回该转换。
        try:
            inv = db.get(Invoice, invoice_id)
            if inv is not None:
                if inv.status == InvoiceStatus.parsed.value:
                    transition(inv, InvoiceStatus.verifying.value)
                if inv.status == InvoiceStatus.verifying.value:
                    transition(inv, InvoiceStatus.pending_review.value)
                    write_audit(
                        db, action="VERIFY", invoice_id=inv.id, channel="system",
                        detail={"result": "error", "error": str(exc)},
                    )
                    db.commit()
        except Exception:
            db.rollback()
            logger.exception("验真失败兜底异常 invoice_id=%s", invoice_id)
        raise
    finally:
        db.close()


async def parse_invoice_task(ctx, invoice_id: int) -> None:
    await asyncio.to_thread(_parse_invoice, invoice_id)


async def verify_invoice_task(ctx, invoice_id: int) -> None:
    # _verify_invoice 在 Task 11 中与本函数同文件定义，运行时解析
    await asyncio.to_thread(_verify_invoice, invoice_id)
