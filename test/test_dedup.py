import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models import Invoice
from invoicing.verify.dedup import find_duplicate


def test_find_duplicate_with_detached_probe(db):
    first = Invoice(invoice_number="24312000000012345678", file_url="a.xml", file_type="XML")
    db.add(first)
    db.flush()
    probe = Invoice(tenant_id="default", invoice_number="24312000000012345678", file_url="b.xml", file_type="XML")
    found = find_duplicate(db, probe)
    assert found is not None
    assert found.id == first.id


def test_find_duplicate_self_excluded(db):
    inv = Invoice(invoice_number="24312000000099998888", file_url="a.xml", file_type="XML")
    db.add(inv)
    db.flush()
    assert find_duplicate(db, inv) is None


def test_dedup_unique_index_backstop(db):
    db.add(Invoice(invoice_number="24312000000077776666", file_url="a.xml", file_type="XML"))
    db.flush()
    db.add(Invoice(invoice_number="24312000000077776666", file_url="b.xml", file_type="XML"))
    with pytest.raises(IntegrityError):
        db.flush()  # 数据库唯一索引兜底并发重复


def test_find_duplicate_null_number_skips(db):
    """回归：号码为空的记录不参与查重（IS NULL 误匹配空壳记录事故）。"""
    from invoicing.models import Invoice

    shell = Invoice(file_url="a.xml", file_type="XML", invoice_number=None, status="blocked")
    db.add(shell)
    db.flush()
    probe = Invoice(tenant_id="default", invoice_code=None, invoice_number=None)
    assert find_duplicate(db, probe) is None
