"""运维告警（运维兜底）：落库 + 冷却防抖 + 复用 notify 送达。

冷却语义（设计 §7）：每 rule_key 取最近一条告警，其 cooldown_until 未到期则
**只落库不推送**；每次落库都会刷新 cooldown_until。恢复不推送（v1，页面可见即闭环）。
"""
import logging
from datetime import datetime, timedelta

from invoicing.config import settings
from invoicing.models.fields import utcnow

logger = logging.getLogger(__name__)


def record_alert(db, rule_key: str, severity: str, message: str,
                 detail: dict | None = None, cooldown: timedelta | None = None) -> bool:
    """写一条告警；返回是否实际推送企微。任何异常不阻断调用方（调用方自行兜底）。"""
    from invoicing.models.ops import OpsAlert
    from invoicing.notify import notify

    cd = cooldown or timedelta(hours=settings.alert_cooldown_hours)
    now = utcnow()
    push = True
    try:
        last = (db.query(OpsAlert).filter(OpsAlert.rule_key == rule_key)
                .order_by(OpsAlert.fired_at.desc()).first())
        if last and last.cooldown_until and last.cooldown_until > now:
            push = False
        db.add(OpsAlert(rule_key=rule_key, severity=severity, message=message,
                        detail=detail, fired_at=now, cooldown_until=now + cd))
        db.commit()
    except Exception:
        logger.exception("告警落库失败 rule=%s", rule_key)
        return False
    if push:
        icon = "🔴" if severity == "critical" else "🟡"
        notify(f"{icon} 发票易运维告警[{severity}] {rule_key}：{message}")
    return push


def _fired(db, rule_key: str, severity: str, message: str, detail=None) -> str:
    record_alert(db, rule_key, severity, message, detail)
    return rule_key


def _rule_task_failed(db) -> list[str]:
    """规则1：近 1h error 记录 ≥3 即触发（时间窗计数，涵盖连续场景）。"""
    from invoicing.models.ops import TaskRun

    fired = []
    hour_ago = utcnow() - timedelta(hours=1)
    for name in ("mailbox_poll", "review_predict", "monthly_health", "audit_retention",
                 "ops_check", "ops_backup", "parse", "verify", "receipt_parse"):
        n = (db.query(TaskRun)
             .filter(TaskRun.task_name == name, TaskRun.outcome == "error",
                     TaskRun.started_at >= hour_ago).count())
        if n >= 3:
            fired.append(_fired(db, f"task_failed.{name}", "critical",
                                f"任务 {name} 近 1 小时失败 {n} 次"))
    return fired


def _rule_mailbox_stalled(db) -> list[str]:
    """规则2：收取邮箱超过 2×轮询间隔未更新。"""
    from invoicing.ops.metrics import stalled_mailboxes

    names = stalled_mailboxes()
    if names:
        return [_fired(db, "mailbox.stalled", "critical",
                       f"收取通道疑似瘫痪：{','.join(names)} 超过 2×轮询间隔未更新")]
    return []


def _rule_backup_missing(db) -> list[str]:
    """规则3：超过 26 小时无新备份。"""
    from invoicing.ops.backup import list_backups

    items = list_backups()
    # list_backups 的 created_at 为 UTC aware ISO；utcnow() 为 naive-UTC，比对前归一化
    fresh = [b for b in items
             if utcnow() - datetime.fromisoformat(b["created_at"]).replace(tzinfo=None)
             < timedelta(hours=26)]
    if not fresh:
        return [_fired(db, "backup.missing", "critical", "超过 26 小时无新备份")]
    return []


def _rule_disk_low(db) -> list[str]:
    """规则4：磁盘剩余低于阈值百分比。"""
    from invoicing.ops.metrics import storage_usage

    pct = storage_usage()["disk_free_percent"]
    if pct < settings.alert_disk_min_percent:
        return [_fired(db, "disk.low", "warning",
                       f"磁盘剩余 {pct}%（<{settings.alert_disk_min_percent}%）")]
    return []


def _rule_review_backlog(db) -> list[str]:
    """规则5：待复核积压超过阈值（持续由 4h 冷却期近似）。"""
    from invoicing.ops.metrics import collect_metrics

    backlog = collect_metrics()["review_backlog"]
    if backlog > settings.alert_review_backlog:
        return [_fired(db, "review.backlog", "warning",
                       f"待复核积压 {backlog} 张（>{settings.alert_review_backlog}）")]
    return []


def _rule_parse_error_rate(db) -> list[str]:
    """规则6：近 1h 解析 error 占比超阈值（且样本数达标）。"""
    from invoicing.models.ops import TaskRun

    hour_ago = utcnow() - timedelta(hours=1)
    q = db.query(TaskRun).filter(TaskRun.task_name == "parse", TaskRun.started_at >= hour_ago)
    total = q.count()
    if total < settings.alert_parse_error_min:
        return []
    errors = q.filter(TaskRun.outcome == "error").count()
    if errors / total > settings.alert_parse_error_rate:
        return [_fired(db, "parse.error_rate", "warning",
                       f"近 1h 解析异常率 {errors}/{total}（>{settings.alert_parse_error_rate:.0%}）")]
    return []


_RULES = [_rule_task_failed, _rule_mailbox_stalled, _rule_backup_missing,
          _rule_disk_low, _rule_review_backlog, _rule_parse_error_rate]


def evaluate_alerts(db) -> list[str]:
    fired: list[str] = []
    for rule in _RULES:
        try:
            fired.extend(rule(db))
        except Exception:
            logger.exception("告警规则执行失败 %s", rule.__name__)
    return fired
