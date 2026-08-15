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


def test_ingest_image_ocr_unavailable(tmp_path, monkeypatch):
    """图片 ingest：OCR 不可用时报安装提示（邮箱拒收语义在 fetch 层，不受影响）。"""
    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: None)
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    with pytest.raises(ValueError, match="OCR 引擎未安装"):
        ingest_invoice(str(p))


def test_ingest_image_with_ocr_full_pipeline(tmp_path, monkeypatch):
    from invoicing.parse.ocr import OcrText

    class FakeProvider:
        def ocr_image(self, image_bytes):
            return OcrText(
                text="发票号码：26617000000309516967\n开票日期：2026年07月09日\n合 计 ¥65.48 ¥1.96\n",
                confidence=0.91,
            )

    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: FakeProvider())
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    inv = ingest_invoice(str(p))
    assert inv.status == "pending_submit"  # 本地内联：OCR 识别 → 解析 → 验真
    assert inv.invoice_number == "26617000000309516967"
    assert inv.file_type == "IMAGE"


def test_ingest_duplicate_number_blocked(db, tmp_path):
    first = ingest_invoice(str(FIXTURES / "dianzi.xml"))
    assert first.status == "pending_submit"
    # 同发票号不同文件再次导入 → 查重拦截
    dup = tmp_path / "dianzi_copy.xml"
    dup.write_bytes((FIXTURES / "dianzi.xml").read_bytes())
    second = ingest_invoice(str(dup))
    assert second.status == "blocked"
    assert second.duplicate_of_id == first.id
