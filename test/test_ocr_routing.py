"""OCR 路由测试（FakeOcrProvider 注入，不依赖真实 paddle）。"""
import io
import zipfile
from decimal import Decimal

from invoicing.parse import ocr as ocr_mod
from invoicing.parse.ocr import OcrText
from invoicing.parse.router import parse_file

OCR_TEXT = (
    "电子发票（普通发票） 发票号码：26617000000309516967\n"
    "开票日期：2026年07月09日\n"
    "名称：测试采购有限公司\n"
    "统一社会信用代码/纳税人识别号：91310000MA1FL0B000\n"
    "名称：示例出行科技有限公司\n"
    "统一社会信用代码/纳税人识别号：91310000MA1FL0A000\n"
    "合    计 ¥65.48 ¥1.96\n"
)


class FakeProvider:
    def ocr_image(self, image_bytes: bytes) -> OcrText:
        return OcrText(text=OCR_TEXT, confidence=0.92)


def _patch_ocr(monkeypatch, provider):
    monkeypatch.setattr(ocr_mod, "get_ocr_provider", lambda: provider)


def test_parse_file_pdf_ocr_fallback(monkeypatch):
    _patch_ocr(monkeypatch, FakeProvider())
    monkeypatch.setattr(ocr_mod, "render_pdf_first_page", lambda b: b"png-bytes")
    outcome = parse_file("PDF", b"%PDF-1.4 no text layer")
    assert outcome.source == "PDF_OCR"
    assert outcome.parsed is not None
    assert outcome.parsed.invoice_number == "26617000000309516967"
    assert outcome.parsed.total_amount == Decimal("67.44")
    assert outcome.parsed.confidence_score == 0.92  # OCR 均值透传
    assert outcome.errors == []


def test_parse_file_ofd_ocr_fallback(monkeypatch):
    _patch_ocr(monkeypatch, FakeProvider())
    monkeypatch.setattr(ocr_mod, "extract_ofd_page_image", lambda b: b"png-bytes")
    # OFD 无内嵌 XML、无 TextCode 文本 → OCR
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD/>")
    outcome = parse_file("OFD", buf.getvalue())
    assert outcome.source == "OFD_OCR"
    assert outcome.parsed.invoice_number == "26617000000309516967"


def test_parse_file_image_type(monkeypatch):
    _patch_ocr(monkeypatch, FakeProvider())
    outcome = parse_file("IMAGE", b"\xff\xd8\xff\xe0")
    assert outcome.source == "IMAGE_OCR"
    assert outcome.parsed.confidence_score == 0.92


def test_parse_file_ocr_unavailable_falls_back(monkeypatch):
    monkeypatch.setattr(ocr_mod, "get_ocr_provider", lambda: None)
    monkeypatch.setattr(ocr_mod, "render_pdf_first_page", lambda b: b"png-bytes")
    outcome = parse_file("PDF", b"%PDF-1.4 no text")
    assert outcome.source == "PDF_UNSTRUCTURED"
    assert outcome.parsed is None


def test_parse_file_ocr_extraction_failure(monkeypatch):
    class GarbageProvider:
        def ocr_image(self, image_bytes: bytes) -> OcrText:
            return OcrText(text="无关文本", confidence=0.9)

    _patch_ocr(monkeypatch, GarbageProvider())
    monkeypatch.setattr(ocr_mod, "render_pdf_first_page", lambda b: b"png-bytes")
    outcome = parse_file("PDF", b"%PDF-1.4")
    assert outcome.source == "PDF_UNSTRUCTURED"  # 提取失败 → 待复核
