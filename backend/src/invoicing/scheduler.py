import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from invoicing.config import settings
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Mailbox
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)


def _due_mailboxes() -> list[int]:
    with SessionLocal() as db:
        mailboxes = db.query(Mailbox).filter(Mailbox.enabled.is_(True)).all()
        now = utcnow()
        due = [
            mb.id
            for mb in mailboxes
            if mb.last_polled_at is None
            or now - mb.last_polled_at >= timedelta(seconds=mb.poll_interval_seconds)
        ]
        return due


def _poll_due_mailboxes() -> None:
    for mailbox_id in _due_mailboxes():
        with SessionLocal() as db:
            mb = db.get(Mailbox, mailbox_id)
            if mb is None:
                continue
            try:
                poll_mailbox(db, mb)
            except Exception:
                logger.exception("定时收取失败 mailbox_id=%s", mailbox_id)


async def _scheduled_poll() -> None:
    await asyncio.to_thread(_poll_due_mailboxes)


def setup_scheduler(app: FastAPI) -> None:
    if not settings.scheduler_enabled:
        return
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_scheduled_poll, "interval", seconds=60, id="mailbox_poll")
    scheduler.start()
    app.state.scheduler = scheduler
