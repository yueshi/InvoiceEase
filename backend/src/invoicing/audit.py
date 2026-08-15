from sqlalchemy.orm import Session

from invoicing.models import AuditLog


def write_audit(
    db: Session,
    action: str,
    user_id: int | None = None,
    invoice_id: int | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    channel: str = "web",
) -> AuditLog:
    log = AuditLog(
        user_id=user_id,
        action=action,
        invoice_id=invoice_id,
        detail=detail,
        ip_address=ip_address,
        channel=channel,
    )
    db.add(log)
    db.flush()
    return log
