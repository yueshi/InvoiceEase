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
            # 完整字段文本：质量门（GATE_FIELDS 缺 ≥2 → 交 LLM/待复核）需放行才能走到验真
            return OcrText(
                text="电子发票（普通发票） 发票号码：26617000000309516967\n"
                     "开票日期：2026年07月09日\n"
                     "名称：测试采购有限公司\n"
                     "统一社会信用代码/纳税人识别号：91310000MA1FL0B000\n"
                     "名称：示例出行科技有限公司\n"
                     "统一社会信用代码/纳税人识别号：91310000MA1FL0A000\n"
                     "合    计 ¥65.48 ¥1.96\n",
                confidence=0.91,
            )

    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: FakeProvider())
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    inv = ingest_invoice(str(p))
    assert inv.status == "pending_submit"  # 本地内联：OCR 识别 → 解析 → 验真
    assert inv.invoice_number == "26617000000309516967"
    assert inv.file_type == "IMAGE"


RECEIPT_TEXT = (
    "中国银行 电子回单\n"
    "交易日期：2026年07月09日\n"
    "付款人户名：示例出行科技有限公司\n"
    "对方户名：测试采购有限公司\n"
    "交易金额：3,500.00\n"
    "摘要：机票款\n"
)


def test_ingest_noninvoice_pdf_with_receipt_features_rejected(db, tmp_path, monkeypatch):
    """银行回单（有文本层、无发票字段）→ 硬拒绝并提示走 receipt_ingest，不留空壳。"""
    # 逐页解析（R1.2）后回单文本走 extract_pdf_pages
    monkeypatch.setattr(
        "invoicing.parse.pdf_text_parser.extract_pdf_pages", lambda data: [RECEIPT_TEXT]
    )
    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: None)
    monkeypatch.setattr("invoicing.parse.llm.get_llm_engine", lambda: None)

    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    before = db.query(Invoice).count()
    with pytest.raises(ValueError, match="receipt_ingest"):
        ingest_invoice(str(p))
    assert db.query(Invoice).count() == before  # 空壳不落库
    logs = db.query(AuditLog).filter(AuditLog.action == "PARSE").all()
    assert any(l.detail.get("result") == "not_invoice" for l in logs)


def test_ingest_noninvoice_pdf_without_receipt_features_rejected(db, tmp_path, monkeypatch):
    """无发票字段且无回单特征 → 硬拒绝（能力受限时说明原因），不留空壳。"""
    monkeypatch.setattr(
        "invoicing.parse.pdf_text_parser.extract_pdf_pages", lambda data: ["周末团建通知，自愿参加。"]
    )
    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: None)
    monkeypatch.setattr("invoicing.parse.llm.get_llm_engine", lambda: None)

    p = tmp_path / "note.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    before = db.query(Invoice).count()
    with pytest.raises(ValueError, match="未识别|OCR"):
        ingest_invoice(str(p))
    assert db.query(Invoice).count() == before


def test_ingest_worker_hard_rejected_surfaces_not_invoice_message(db, tmp_path, monkeypatch):
    """本地模式 worker 已硬拒绝（记录物理删除）→ 工具报「非发票」而非误判「重复」。"""
    from invoicing.parse.schemas import ParseOutcome

    monkeypatch.setattr(
        "invoicing.workers.tasks.parse_file",
        lambda ft, data: ParseOutcome(source="PDF_UNSTRUCTURED", parsed=None, errors=[]),
    )
    # 识别能力在位（LLM 引擎可用）：整链零提取 → worker 物理删除
    monkeypatch.setattr("invoicing.parse.llm.get_llm_engine", lambda: object())

    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(ValueError, match="不是电子发票原件"):
        ingest_invoice(str(p))
    assert db.query(Invoice).count() == 0


def test_ingest_duplicate_returns_existing(db, tmp_path):
    first = ingest_invoice(str(FIXTURES / "dianzi.xml"))
    assert first.status == "pending_submit"
    # 同发票号不同文件再次导入 → 查重拦截（新记录物理删除），返回已有记录
    dup = tmp_path / "dianzi_copy.xml"
    dup.write_bytes((FIXTURES / "dianzi.xml").read_bytes())
    second = ingest_invoice(str(dup))
    assert second.id == first.id  # 返回已有记录而非空壳
