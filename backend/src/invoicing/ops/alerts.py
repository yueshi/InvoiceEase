"""运维告警（运维兜底）：落库 + 冷却防抖 + 复用 notify 送达。

冷却语义（设计 §7）：每 rule_key 取最近一条告警，其 cooldown_until 未到期则
**只落库不推送**；每次落库都会刷新 cooldown_until。恢复不推送（v1，页面可见即闭环）。
"""
import logging
from datetime import timedelta

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
