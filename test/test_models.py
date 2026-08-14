from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models import Invoice, User


def test_insert_and_query_invoice(db):
    user = User(username="u1", password_hash="x", role="finance_staff")
    db.add(user)
    db.flush()
    inv = Invoice(
        invoice_number="24312000000012345678",
        issue_date=date(2026, 8, 1),
        total_amount=Decimal("1000.00"),
        file_url="u1/file.pdf",
        file_type="PDF",
        mailbox_id=None,
        email_message_id="<msg1@example.com>",
        email_subject="发票",
    )
    db.add(inv)
    db.flush()
    assert inv.id is not None
    assert inv.status == "received"


def test_duplicate_dedup_key_raises_integrity_error(db):
    db.add(Invoice(invoice_number="24312000000011112222", file_url="a.xml", file_type="XML"))
    db.flush()
    dup = Invoice(invoice_number="24312000000011112222", file_url="b.xml", file_type="XML")
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()


def test_duplicate_email_message_id_raises_integrity_error(db):
    db.add(Invoice(file_url="a.xml", file_type="XML", email_message_id="<m1@x.com>"))
    db.flush()
    db.add(Invoice(file_url="b.xml", file_type="XML", email_message_id="<m1@x.com>"))
    with pytest.raises(IntegrityError):
        db.flush()
