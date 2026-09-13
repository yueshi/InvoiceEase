from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    action: str
    invoice_id: int | None
    detail: dict | None
    outcome: str | None
    ip_address: str | None
    channel: str
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int
