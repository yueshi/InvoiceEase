"""WorkBuddy 归档闭环工具测试（集成设计 V0.1 §三 invoice_ingest）。"""
from pathlib import Path

import pytest

from invoicing.db import SessionLocal
from invoicing.mcp.tools import ingest_invoice
from invoicing.models import AuditLog, Invoice

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_ingest_xml_full_pipeline(db):
    inv = ingest_invoice(str(FIXTURES / "dianzi.xml"))
    assert inv.status == "pending_submit"  # 本地队列模式：内联 parse+verify
    assert inv.invoice_number == "24312000000012345678"
    assert inv.total_amount is not None
    assert inv.xml_url is not None  # XML 原件归档
    with SessionLocal() as s:
        log = s.query(AuditLog).filter(AuditLog.action == "INGEST", AuditLog.invoice_id == inv.id).first()
    assert log is not None
    assert log.channel == "mcp"


def test_ingest_image_rejected(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    with pytest.raises(ValueError, match="合规拒收"):
        ingest_invoice(str(p))


def test_ingest_duplicate_number_blocked(db, tmp_path):
    first = ingest_invoice(str(FIXTURES / "dianzi.xml"))
    assert first.status == "pending_submit"
    # 同发票号不同文件再次导入 → 查重拦截
    dup = tmp_path / "dianzi_copy.xml"
    dup.write_bytes((FIXTURES / "dianzi.xml").read_bytes())
    second = ingest_invoice(str(dup))
    assert second.status == "blocked"
    assert second.duplicate_of_id == first.id
