from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import Money, utcnow


class BankReceipt(Base):
    """银行回单（数字员工 P3/R1）：支出凭证，与发票配对建议（不接银行 API）。"""

    __tablename__ = "bank_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)

    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    counterparty_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    abstract: Mapped[str | None] = mapped_column(String(256), nullable=True)

    paired_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/paired/unmatched

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow,
        onupdate=func.now(), nullable=False,
    )
