"""OCR 基础设施测试（Fake 注入，不依赖真实 paddle）。"""
import io
import sys
import zipfile

from invoicing.parse import ocr as ocr_mod
from invoicing.parse.ocr import OcrText, extract_ofd_page_image, render_pdf_first_page


class FakeProvider:
    def __init__(self, text: OcrText | None):
        self.text = text
        self.calls = 0

    def ocr_image(self, image_bytes: bytes) -> OcrText | None:
        self.calls += 1
        return self.text


def test_render_pdf_import_missing_returns_none(monkeypatch):
    # pymupdf 未安装（optional 依赖缺失）→ None 而非异常
    monkeypatch.setitem(sys.modules, "fitz", None)
    assert render_pdf_first_page(b"%PDF-1.4") is None


def test_extract_ofd_page_image_largest_png():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD/>")
        zf.writestr("Doc_0/Res/small.png", b"x" * 100)
        zf.writestr("Doc_0/Res/big.png", b"y" * 500)
    img = extract_ofd_page_image(buf.getvalue())
    assert img == b"y" * 500


def test_extract_ofd_page_image_none_without_png():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD/>")
    assert extract_ofd_page_image(buf.getvalue()) is None


def test_extract_ofd_bad_zip_none():
    assert extract_ofd_page_image(b"not a zip") is None


def test_get_ocr_provider_none_when_paddleocr_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    monkeypatch.setattr(ocr_mod, "_tried", False)
    monkeypatch.setattr(ocr_mod, "_provider", None)
    assert ocr_mod.get_ocr_provider() is None
    assert ocr_mod._tried is True


def test_paddle_provider_lazy_init_failure_latch(monkeypatch):
    provider = ocr_mod.PaddleOcrProvider()
    # 模拟 import 成功但初始化失败（如模型下载失败）
    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("model download failed")

    monkeypatch.setitem(sys.modules, "paddleocr", type("M", (), {"PaddleOCR": Boom})())
    assert provider.ocr_image(b"img") is None
    assert provider._init_failed is True
    # 失败闩：不再重复尝试
    assert provider.ocr_image(b"img") is None
