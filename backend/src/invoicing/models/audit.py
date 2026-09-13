from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 结果类别（success/blocked/failed/error/None）：审计「事件是否成功」要素，
    # 写入时归一化落库，供「仅看异常」筛选与统计
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
