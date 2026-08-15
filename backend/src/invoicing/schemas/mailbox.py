from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MailboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    username: str
    folder: str
    keywords: str
    poll_interval_seconds: int
    smtp_host: str | None
    smtp_port: int | None
    smtp_username: str | None
    enabled: bool
    last_polled_at: datetime | None
    last_uid: int
    created_at: datetime
    updated_at: datetime


class MailboxCreate(BaseModel):
    name: str
    imap_host: str
    imap_port: int = 993
    use_ssl: bool = True
    username: str
    password: str
    folder: str = "INBOX"
    keywords: str = "发票,Invoice"
    poll_interval_seconds: int = 300
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None


class MailboxUpdate(BaseModel):
    name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    use_ssl: bool | None = None
    username: str | None = None
    password: str | None = None
    folder: str | None = None
    keywords: str | None = None
    poll_interval_seconds: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    enabled: bool | None = None


class PollResultOut(BaseModel):
    received: int
    rejected_images: int
    ignored: int
    duplicates: int
    errors: int
