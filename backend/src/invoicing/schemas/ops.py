"""运维端点 schema（设计 §9）。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TaskRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_name: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    outcome: str
    error: str | None = None
    detail: dict | None = None


class TaskRunListResponse(BaseModel):
    items: list[TaskRunOut]
    total: int
    page: int
    page_size: int


class OpsAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_key: str
    severity: str
    message: str
    detail: dict | None = None
    fired_at: datetime
    cooldown_until: datetime | None = None


class OpsAlertListResponse(BaseModel):
    items: list[OpsAlertOut]
    total: int
    page: int
    page_size: int


class BackupOut(BaseModel):
    name: str
    size_bytes: int
    created_at: str
    meta: dict | None = None


class OpsCheckOut(BaseModel):
    name: str
    level: str
    message: str


class OpsStatusOut(BaseModel):
    version: str
    uptime_seconds: int
    checks: list[OpsCheckOut]
    metrics: dict
    storage: dict
    last_backup: BackupOut | None = None
