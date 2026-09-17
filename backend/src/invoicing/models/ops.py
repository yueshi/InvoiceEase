"""运维兜底两表：任务执行记录与告警历史（design/2026-09-16-运维兜底设计.md §3）。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class TaskRun(Base):
    """任务执行记录：班表/队列/手动统一埋点。

    outcome 语义：success=正常完成（轮询 0 新邮件也算）；failed=业务性未达预期；
    error=异常抛出；missed=调度器判定错过未执行。
    """

    __tablename__ = "task_runs"
    __table_args__ = (Index("ix_task_runs_name_started", "task_name", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # scheduler/manual/enqueue
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)  # missed 无耗时
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # 类型名+消息，不泄敏感值
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OpsAlert(Base):
    """告警历史：规则触发落库 + 冷却防抖（冷却期内只落库不推送）。"""

    __tablename__ = "ops_alerts"
    __table_args__ = (Index("ix_ops_alerts_rule_fired", "rule_key", "fired_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # critical/warning
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
