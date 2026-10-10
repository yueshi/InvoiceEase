import logging

from sqlalchemy.orm import Session

from invoicing.config import settings
from invoicing.models import Role, User
from invoicing.security import hash_password

logger = logging.getLogger(__name__)


def ensure_admin_user(db: Session) -> None:
    if db.query(User).count() > 0:
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            role=Role.admin.value,
        )
    )
    db.commit()


def ensure_default_policies(db: Session) -> None:
    """幂等播种默认费用标准（P1）。失败只告警不阻断启动——校验工具会
    以降级为 warning 的方式容忍缺政策（见 validators.check_meal）。"""
    from invoicing.workflow.policy_seed import seed_default_policies

    try:
        created = seed_default_policies(db)
        if created:
            logger.info("已播种默认费用标准 %d 条", created)
    except Exception:  # noqa: BLE001 —— 启动期故障不应阻断服务
        logger.warning("默认费用标准播种失败（不影响启动）", exc_info=True)
