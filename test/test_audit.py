from invoicing.audit import write_audit
from invoicing.models import AuditLog, Invoice, User


def test_write_audit(db):
    user = User(username="u1", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    inv = Invoice(file_url="a.xml", file_type="XML")
    db.add(inv)
    db.flush()

    log = write_audit(
        db,
        action="REVIEW",
        user_id=user.id,
        invoice_id=inv.id,
        detail={"note": "通过"},
        ip_address="127.0.0.1",
        channel="web",
    )
    assert log.id is not None
    assert log.action == "REVIEW"
    assert log.detail == {"note": "通过"}
    assert log.channel == "web"


def test_write_audit_system_channel(db):
    log = write_audit(db, action="FETCH", channel="system", detail={"mailbox_id": 1})
    assert log.user_id is None
    assert log.channel == "system"
