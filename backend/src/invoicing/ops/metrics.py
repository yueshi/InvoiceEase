"""指标聚合（运维兜底）：现场查询，无快照表（设计 §2）。供 /ops/status 与告警规则共用。"""
import shutil
from datetime import timedelta
from pathlib import Path

from invoicing.config import settings
from invoicing.db import SessionLocal
from invoicing.models import Invoice, Mailbox
from invoicing.models.fields import utcnow
from invoicing.ops.instrumentation import PROCESS_STARTED_AT


def collect_metrics() -> dict:
    with SessionLocal() as db:
        day_ago = utcnow() - timedelta(hours=24)
        recent = db.query(Invoice).filter(Invoice.created_at >= day_ago)
        backlog = db.query(Invoice).filter(Invoice.status == "pending_review").count()
        return {
            "process_started_at": PROCESS_STARTED_AT.isoformat(),
            "last_24h": {
                "created": recent.count(),
                "verify_passed": recent.filter(Invoice.verify_status == "passed").count(),
                "pending_review": backlog,
                "blocked": db.query(Invoice).filter(Invoice.status == "blocked").count(),
            },
            "review_backlog": backlog,
        }


def storage_usage() -> dict:
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    originals = Path(settings.storage_root)
    originals_bytes = sum(f.stat().st_size for f in originals.rglob("*") if f.is_file()) \
        if originals.exists() else 0
    anchor = originals.resolve().anchor or "/"
    usage = shutil.disk_usage(anchor)
    return {
        "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "originals_bytes": originals_bytes,
        "disk_free_percent": round(100 * usage.free / usage.total, 1) if usage.total else 100.0,
    }


def stalled_mailboxes() -> list[str]:
    """enabled 且 last_polled_at 超过 2×poll_interval 未更新的邮箱名列表。"""
    with SessionLocal() as db:
        now = utcnow()
        out = []
        for mb in db.query(Mailbox).filter(Mailbox.enabled.is_(True)).all():
            deadline = mb.poll_interval_seconds * 2
            if mb.last_polled_at is None or (now - mb.last_polled_at).total_seconds() > deadline:
                out.append(mb.name)
        return out
