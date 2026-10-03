"""Web 免登票据兑换记录：jti 主键 = 一次性抢占。

设计见 design/2026-10-03-Agent跳转Web后台-深链与免登票据设计.md §5：
- 签发票据**不写库**（只签 JWT）；兑换时 INSERT 本行，主键冲突即「已用过」→ 401
- 过期行惰性清理（兑换时顺手删），量级极小，无需 cron
- 承兑后签发的是该用户普通会话 token，权限不放大
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class WebTicket(Base):
    __tablename__ = "web_tickets"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)  # 票据唯一 id（uuid4 hex）
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
