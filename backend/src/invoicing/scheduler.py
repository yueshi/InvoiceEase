"""调度器任务目录：数字员工的「班表」。

每个任务 = 一项岗位职责（task_id 即职责名）；setup_scheduler 按注册表
统一建 AsyncIOScheduler job。注册表项存 trigger 类型与参数：
interval 轮询用于高频检查（收信/预判），cron 时刻表用于定期汇报（P3，
结构已预留）。新增任务用 register_task 挂入即可。
"""
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

# task_id → {"fn": Callable, "trigger": "interval" | "cron", "trigger_kwargs": dict}
TASKS: dict[str, dict] = {}


def register_task(task_id: str, fn, trigger: str = "interval", **trigger_kwargs) -> None:
    """注册班表任务（幂等覆盖）。trigger_kwargs 按 APScheduler 语义：
    interval → seconds=60；cron → hour=9, day_of_week="mon" 等。"""
    TASKS[task_id] = {"fn": fn, "trigger": trigger, "trigger_kwargs": trigger_kwargs}


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


register_task("mailbox_poll", _scheduled_poll, seconds=60)


def _generate_review_predictions() -> None:
    from invoicing.parse.ai_review import generate_missing_predictions

    try:
        processed = generate_missing_predictions()
        if processed:
            logger.info("复核预判生成 %s 张", processed)
    except Exception:
        logger.exception("复核预判任务异常")


register_task("review_predict", _generate_review_predictions, seconds=60)


def setup_scheduler(app: FastAPI) -> None:
    if not settings.scheduler_enabled:
        return
    scheduler = AsyncIOScheduler()
    for task_id, spec in TASKS.items():
        scheduler.add_job(spec["fn"], spec["trigger"], id=task_id, **spec["trigger_kwargs"])
    scheduler.start()
    app.state.scheduler = scheduler
