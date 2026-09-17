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
            except Exception as exc:
                logger.exception("定时收取失败 mailbox_id=%s", mailbox_id)
                from invoicing.ops.instrumentation import record_failure

                record_failure("mailbox_poll", exc, {"mailbox_id": mailbox_id})
                from invoicing.notify import notify

                notify(f"🔴 班表任务异常：mailbox_poll mailbox_id={mailbox_id}（{type(exc).__name__}）")


async def _scheduled_poll() -> None:
    await asyncio.to_thread(_poll_due_mailboxes)


register_task("mailbox_poll", _scheduled_poll, seconds=60)


def _generate_review_predictions() -> None:
    from invoicing.parse.ai_review import auto_review_predictions, generate_missing_predictions

    try:
        processed = generate_missing_predictions()
        if processed:
            logger.info("复核预判生成 %s 张", processed)
        # 渐进自主（M8）：阈值 > 0 时同轮自动执行 approve 方向（拦截永不自动）
        if settings.auto_review_threshold > 0:
            with SessionLocal() as db:
                n = auto_review_predictions(db, settings.auto_review_threshold)
                if n:
                    logger.info("渐进自主自动通过 %s 张（阈值 %s）", n, settings.auto_review_threshold)
    except Exception as exc:
        logger.exception("复核预判任务异常")
        from invoicing.ops.instrumentation import record_failure

        record_failure("review_predict", exc)
        from invoicing.notify import notify

        notify(f"🔴 班表任务异常：review_predict（{type(exc).__name__}）")


register_task("review_predict", _generate_review_predictions, seconds=60)


def _monthly_health_report() -> None:
    """每月 1 日生成健康报告（P3/R3）：首次启用 cron 结构。

    注意：本地开发进程不常驻，此任务依赖 FastAPI 进程存活；生产推荐由
    WorkBuddy 自动化（每月 1 日调 invoice_health_report）兜底双轨。
    """
    from datetime import date, timedelta

    from invoicing.reports import monthly_health

    try:
        month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        with SessionLocal() as db:
            text = monthly_health(db, month)
        from invoicing.notify import notify

        notify(text)
        logger.info("月度健康报告已生成 month=%s", month)
    except Exception as exc:
        logger.exception("月度健康报告任务异常")
        from invoicing.ops.instrumentation import record_failure

        record_failure("monthly_health", exc)
        from invoicing.notify import notify

        notify(f"🔴 班表任务异常：monthly_health（{type(exc).__name__}）")


register_task("monthly_health", _monthly_health_report, trigger="cron", day=1, hour=9)


def _purge_expired_login_audits(db, retention_days: int | None = None) -> int:
    """清理过期的登录成功审计行（失败行与业务操作长期保留），返回删除条数。"""
    from datetime import datetime, timedelta, timezone

    from invoicing.models import AuditLog

    days = retention_days if retention_days is not None else settings.audit_login_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = db.query(AuditLog).filter(AuditLog.action == "LOGIN", AuditLog.created_at < cutoff)
    count = q.count()
    if count:
        q.delete(synchronize_session=False)
        db.commit()
    return count


def _scheduled_audit_retention() -> None:
    with SessionLocal() as db:
        try:
            removed = _purge_expired_login_audits(db)
            if removed:
                logger.info("审计保留期清理：删除 %s 条历史登录行", removed)
        except Exception:
            logger.exception("审计保留期清理失败")


# 每日 03:17（避开整点与业务高峰；登录成功行按 audit_login_retention_days 清理）
register_task("audit_retention", _scheduled_audit_retention, trigger="cron", hour=3, minute=17)


def _install_missed_listener(scheduler) -> None:
    """EVENT_JOB_MISSED → task_runs outcome=missed。"""
    from apscheduler.events import EVENT_JOB_MISSED

    from invoicing.ops.instrumentation import _write_row

    def _on_missed(event) -> None:
        _write_row(str(getattr(event, "job_id", "?")), "scheduler", utcnow(), None,
                   "missed", None, {"scheduled_time": str(getattr(event, "scheduled_time", ""))})

    scheduler.add_listener(_on_missed, EVENT_JOB_MISSED)


def setup_scheduler(app: FastAPI) -> None:
    if not settings.scheduler_enabled:
        return
    from invoicing.ops.instrumentation import wrap_job

    scheduler = AsyncIOScheduler()
    for task_id, spec in TASKS.items():
        scheduler.add_job(
            wrap_job(task_id, spec["fn"]), spec["trigger"], id=task_id,
            misfire_grace_time=3600 if spec["trigger"] == "cron" else 300,
            coalesce=True, **spec["trigger_kwargs"],
        )
    _install_missed_listener(scheduler)
    scheduler.start()
    app.state.scheduler = scheduler
