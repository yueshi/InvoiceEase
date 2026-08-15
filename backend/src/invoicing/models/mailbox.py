from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class Mailbox(Base):
    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mailbox_type: Mapped[str] = mapped_column(String(16), nullable=False, default="imap")
    agently_workspace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agently_token_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    imap_host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    folder: Mapped[str] = mapped_column(String(128), nullable=False, default="INBOX")
    keywords: Mapped[str] = mapped_column(String(256), nullable=False, default="发票,Invoice")
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    smtp_host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    smtp_password_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_polled_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_uid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=func.now(),
        nullable=False,
    )
