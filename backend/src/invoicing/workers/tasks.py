import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.models import BankReceipt, Invoice, InvoiceStatus, ReceiptUpload, VerifyStatus
from invoicing.models.fields import utcnow
from invoicing.parse.router import parse_file
from invoicing.storage import get_storage
from invoicing.verify.dedup import find_duplicate
from invoicing.verify.provider import get_provider
from invoicing.workflow.services import _cleanup_dependents_of
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


def _parse_chain_armed() -> bool:
    """识别能力是否在位（OCR 或 LLM 任一可用）。

    全链未武装时（如仅装基础依赖的开发环境），无文本层 PDF 的「空解析」无法区分
    「非发票文档」与「引擎缺失」，宁可走待复核不误删（发票绝不丢）。
    """
    from invoicing.parse.llm import get_llm_engine
    from invoicing.parse.ocr import get_ocr_provider

    return get_ocr_provider() is not None or get_llm_engine() is not None


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

        if outcome.parsed is None and not outcome.errors and _parse_chain_armed():
            # 非发票硬拒绝：识别能力在位但整链（XBRL/文本层/OCR/VLM）零提取 →
            # 不是电子发票原件（如银行回单、通知函），物理删除不留空壳污染发票库。
            # 审计先写：记录删除后 invoice_id 经 FK SET NULL 置空，明细仍可追溯。
            write_audit(
                db, action="PARSE", invoice_id=inv.id, channel="system",
                detail={
                    "result": "not_invoice",
                    "source": outcome.source,
                    "file_type": inv.file_type,
                    "file_name": (inv.file_url or "").rsplit("/", 1)[-1],
                    # 记录删除后 invoice_id 经 FK SET NULL 置空，凭此键回溯本次拒收
                    # （MCP ingest 重载 inv is None 时据此区分「非发票拒收」与「重复拦截」）
                    "discarded_invoice_id": invoice_id,
                },
            )
            _cleanup_dependents_of(db, inv.id, unlink_receipts=True)  # 本记录即将物理删除
            for key in (inv.file_url, inv.xml_url):
                if not key:
                    continue
                try:
                    storage.delete(key)
                except Exception:
                    logger.warning("非发票拒收原件删除失败 invoice_id=%s key=%s", inv.id, key, exc_info=True)
            db.delete(inv)
            db.commit()
            from invoicing.notify import notify

            notify(f"🚫 非发票文档已拒收：整链解析未提取出发票字段（{outcome.source}）")
            return

        # 纠错字典 + 购买方归属校验追加错误：仅成功分支填充（confidence<1.0 门控在 enrich_parsed 内部）
        extra: list = []
        if outcome.parsed is not None and not outcome.errors:
            from invoicing.parse.company_dict import enrich_parsed
            from invoicing.parse.red_flag import detect_red_invoice

            extra = enrich_parsed(outcome.parsed, db)
            parsed_code = outcome.parsed.invoice_code
            parsed_number = outcome.parsed.invoice_number
            _apply_parsed_fields(inv, outcome.parsed)
            _save_xml_original(storage, inv, outcome.xml_data)
            # 自动类型标签（规则优先；未命中留空 = 列表显示「未归类」，不臆测）
            if not inv.expense_type:
                from invoicing.parse.classify import rule_suggest_expense_type

                hit = rule_suggest_expense_type(inv.seller_name, inv.invoice_type)
                if hit:
                    inv.expense_type = hit
            if extra:
                # BUYER_MISMATCH 从严：购买方与预设本司不匹配 → 待复核
                inv.validation_errors = [{"code": e.code, "message": e.message} for e in extra]
                transition(inv, InvoiceStatus.pending_review.value)
                write_audit(
                    db, action="PARSE", invoice_id=inv.id, channel="system",
                    detail={"source": outcome.source, "errors": inv.validation_errors},
                )
            elif detect_red_invoice(None, inv.invoice_type):
                # 红字票（M9 第一步）：识别+标记+待复核，不自动对冲（规则财务确认后另立）
                inv.red_flag = True
                inv.validation_errors = [
                    {"code": "RED_INVOICE", "message": "红字发票，请人工核对冲销对象"}
                ]
                transition(inv, InvoiceStatus.pending_review.value)
                write_audit(
                    db, action="PARSE", invoice_id=inv.id, channel="system",
                    detail={"source": outcome.source, "red_flag": True},
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
        if outcome.parsed is not None and not outcome.errors and not extra and not inv.red_flag:
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
            # 审计挂在已有原票（existing.id）上——被丢弃的重复没有 invoice_id 语义，
            # 否则 PRAGMA foreign_keys=ON 时 audit_logs.invoice_id FK 拦 db.delete(inv)
            write_audit(
                db, action="PARSE", invoice_id=existing.id, channel="system",
                detail={
                    "result": "duplicate",
                    "duplicate_of_id": existing.id,
                    "discarded_invoice_id": inv.id,
                    "invoice_number": parsed_number,
                },
            )
            from invoicing.notify import notify

            notify(f"🚫 重复拦截：`{parsed_number}` 与已有发票 #{existing.id} 重复")
            # C1 根因修复：删原票前先清指向它的 dependents，避免悬空 FK
            cleared = _cleanup_dependents_of(db, existing.id)
            if cleared:
                write_audit(
                    db, action="PARSE", invoice_id=existing.id, channel="system",
                    detail={
                        "result": "duplicate_cleanup",
                        "cleared_dependents": cleared,
                        "reason": "原票被物理删除前清理悬空 duplicate_of_id",
                    },
                )
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

        provider = get_provider()
        result = provider.verify(inv)
        # mock 标志结构化落库（verify_is_mock 判定不依赖 reason 文案，见 schemas/invoice.py）
        inv.verify_detail = {
            "status": result.status,
            "mock": bool(getattr(provider, "is_mock", False)),
            **result.detail,
        }
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


def _parse_receipt_upload(upload_id: int) -> None:
    """回单上传批次异步解析（R1.1）：读原件 → 分块解析 → 逐张入库+配对 → 更新批次状态。

    在后台线程/arq worker 中执行，上传请求不等待（同步解析 17 块 LLM 兜底可达 2 分钟）。
    """
    from invoicing.parse.receipt import (
        parse_receipts_bytes,
        self_account_set,
        self_name_set,
        suggest_pair,
    )

    db = SessionLocal()
    try:
        up = db.get(ReceiptUpload, upload_id)
        if up is None or up.status != "parsing":
            logger.info("跳过回单解析任务 upload_id=%s status=%s", upload_id, up.status if up else None)
            return
        data = get_storage().get(up.file_url)
        # P1：本司账号/名称集合——判定「本司账户行」与过滤 LLM 误填本司名
        names = self_name_set(db)
        rows = parse_receipts_bytes(
            data, up.file_type,
            self_accounts=self_account_set(db), self_names=names,
        )
        if not rows:
            up.status = "failed"
            up.error = "未识别出银行回单信息（规则+LLM 均未提取出金额与户名）"
            db.commit()
            from invoicing.notify import notify

            notify("🚫 回单解析失败：整份文件未识别出任何回单")
            return
        for fields in rows:
            issues = fields.get("quality_issues") or []
            r = BankReceipt(
                file_url=up.file_url,
                file_type=up.file_type,
                file_hash=up.file_hash,
                user_id=up.user_id,
                trade_date=fields.get("trade_date"),
                counterparty_name=fields.get("counterparty_name"),
                amount=fields.get("amount"),
                abstract=fields.get("abstract"),
                direction=fields.get("direction"),
                quality_issues=issues or None,
                needs_review=bool(issues),
                bank_code=fields.get("bank_code"),
                page_no=fields.get("page"),
                anchor=fields.get("anchor"),
                status="pending",
            )
            # 落库即分类（设计 §4）：新回单零延迟带性质，无需等重分类任务
            from invoicing.workflow.receipts import classify_receipt

            r.category, r.category_source = classify_receipt(r, names)
            db.add(r)
            db.flush()
            suggested = suggest_pair(db, r.id)
            if suggested is not None:
                r.paired_invoice_id = suggested
                r.status = "paired"
            else:
                r.status = "unmatched"
        up.status = "parsed"
        up.receipt_count = len(rows)
        up.parsed_at = utcnow()
        up.error = None
        db.commit()
        from invoicing.notify import notify

        notify(f"✅ 回单解析完成：入库 {len(rows)} 张（批次 #{up.id}）")
    except Exception as exc:
        db.rollback()
        logger.exception("回单解析任务异常 upload_id=%s", upload_id)
        try:
            up = db.get(ReceiptUpload, upload_id)
            if up is not None and up.status == "parsing":
                up.status = "failed"
                up.error = str(exc)[:512]
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("回单解析失败兜底异常 upload_id=%s", upload_id)
        raise
    finally:
        db.close()


async def parse_invoice_task(ctx, invoice_id: int) -> None:
    from invoicing.ops.instrumentation import record_run_async

    await record_run_async("parse", "enqueue", _parse_invoice, invoice_id)


async def verify_invoice_task(ctx, invoice_id: int) -> None:
    # _verify_invoice 在 Task 11 中与本函数同文件定义，运行时解析
    from invoicing.ops.instrumentation import record_run_async

    await record_run_async("verify", "enqueue", _verify_invoice, invoice_id)


async def receipt_parse_task(ctx, upload_id: int) -> None:
    from invoicing.ops.instrumentation import record_run_async

    await record_run_async("receipt_parse", "enqueue", _parse_receipt_upload, upload_id)
