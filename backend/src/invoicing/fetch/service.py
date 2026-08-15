import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.fetch.filters import classify_attachment, unpack_zip
from invoicing.fetch.imap import ImapMailFetcher
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.fetch.reply import send_reject_reply
from invoicing.models import Invoice, Mailbox
from invoicing.models.fields import utcnow
from invoicing.storage import get_storage
from invoicing.workers.queue import enqueue_parse_sync

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    received: int = 0
    rejected_images: int = 0
    ignored: int = 0
    duplicates: int = 0
    errors: int = 0


def _subject_matches(mailbox: Mailbox, subject: str) -> bool:
    keywords = [k.strip() for k in mailbox.keywords.split(",") if k.strip()]
    if not keywords:
        return True
    return any(k in subject for k in keywords)


def _store_original(db: Session, storage, mb: Mailbox, msg: RawMailMessage, att: RawAttachment) -> int | None:
    # 说明：收取阶段 invoice_number 尚未解析，查重唯一索引不触发；此处 IntegrityError
    # 只可能是 email_message_id 唯一索引——即同一邮件被重复收取，忽略并留痕。
    # 注意：add 必须在 savepoint 内部——savepoint 回滚后对象才会从会话中移除，
    # 否则 except 内 write_audit 的 flush 会再次插入已回滚的发票（PendingRollbackError）。
    key = f"tenant-default/mailbox-{mb.id}/{msg.uid}-{att.filename}"
    storage.put(key, att.data, att.content_type or "application/octet-stream")
    inv = Invoice(
        mailbox_id=mb.id,
        email_message_id=msg.message_id,
        email_subject=msg.subject,
        file_url=key,
        file_type=classify_attachment(att.filename, att.content_type, att.data),
        # 收取即交付解析流水线：parse worker 守卫要求 parsing 状态（本地内联与 redis worker 一致）
        status="parsing",
    )
    try:
        with db.begin_nested():
            db.add(inv)
            db.flush()
        return inv.id
    except IntegrityError:
        write_audit(
            db, action="FETCH", channel="system",
            detail={"result": "duplicate_email", "message_id": msg.message_id},
        )
        return None


def _process_attachment(db, storage, mb, msg, att, result: PollResult, to_enqueue: list[int]) -> None:
    kind = classify_attachment(att.filename, att.content_type, att.data)
    if kind in ("PDF", "OFD", "XML"):
        inv_id = _store_original(db, storage, mb, msg, att)
        if inv_id is not None:
            result.received += 1
            to_enqueue.append(inv_id)
        else:
            result.duplicates += 1
    elif kind == "ZIP":
        inner = unpack_zip(att.data)
        has_invoice = False
        for filename, data in inner:
            inner_att = RawAttachment(filename, "", data)
            if classify_attachment(filename, "", data) in ("PDF", "OFD", "XML"):
                has_invoice = True
                inv_id = _store_original(db, storage, mb, msg, inner_att)
                if inv_id is not None:
                    result.received += 1
                    to_enqueue.append(inv_id)
                else:
                    result.duplicates += 1
        if not has_invoice:
            result.ignored += 1
            write_audit(db, action="FETCH", channel="system",
                        detail={"result": "zip_no_invoice", "message_id": msg.message_id})
    elif kind == "IMAGE":
        result.rejected_images += 1
        write_audit(db, action="REJECT_REPLY", channel="system",
                    detail={"message_id": msg.message_id, "filename": att.filename, "reason": "image_not_accepted"})
        try:
            send_reject_reply(mb, msg.sender, msg.subject, provider_message_id=msg.provider_message_id)
        except Exception:
            logger.exception("拒收回复发送失败 message_id=%s", msg.message_id)
            result.errors += 1
    else:
        result.ignored += 1
        write_audit(db, action="FETCH", channel="system",
                    detail={"result": "ignored_attachment", "message_id": msg.message_id,
                            "filename": att.filename})


def poll_mailbox(db: Session, mailbox: Mailbox, fetcher: MailFetcher | None = None) -> PollResult:
    if fetcher is None:
        if mailbox.mailbox_type == "agently":
            from invoicing.fetch.agently import AgentlyFetcher

            fetcher = AgentlyFetcher(mailbox)
        else:
            fetcher = ImapMailFetcher(mailbox)
    result = PollResult()
    to_enqueue: list[int] = []
    try:
        messages = fetcher.fetch_new(mailbox.last_uid)
        for msg in messages:
            if not _subject_matches(mailbox, msg.subject):
                result.ignored += 1
                continue
            for att in msg.attachments:
                _process_attachment(db, get_storage(), mailbox, msg, att, result, to_enqueue)
        mailbox.last_polled_at = utcnow()
        if messages:
            mailbox.last_uid = max(m.uid for m in messages)
        db.commit()
        try:
            fetcher.mark_seen([m.uid for m in messages])
        except Exception:
            logger.exception("IMAP 标记已读失败 mailbox_id=%s", mailbox.id)
        write_audit(db, action="FETCH", channel="system",
                    detail={"mailbox_id": mailbox.id, "result": result.__dict__})
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("收取失败 mailbox_id=%s", mailbox.id)
        result.errors += 1
        write_audit(db, action="FETCH", channel="system",
                    detail={"mailbox_id": mailbox.id, "result": "error"})
        db.commit()
    for inv_id in to_enqueue:
        enqueue_parse_sync(inv_id)
    return result
