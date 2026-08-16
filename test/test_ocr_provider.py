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


def test_idle_reset_drops_stale_engine(monkeypatch):
    """闲置超阈值：丢弃旧引擎强制重新加载（避免长时间空闲后推理卡死）。"""
    from invoicing.parse.ocr import PaddleOcrProvider

    provider = PaddleOcrProvider()
    provider._engine = object()  # 模拟已加载的旧引擎
    provider._last_used = 0.0  # 很久以前用过 → 判闲置
    provider._ocr_uncached = lambda img: OcrText(text="x", confidence=1.0)  # 走重置后的路径
    result = provider.ocr_image(b"img-idle")
    assert result is not None
    assert provider._engine is None  # 旧引擎被丢弃（未重新加载，由下一次 _ensure_engine 惰性重建）
    assert provider._last_used > 0.0


def test_idle_reset_fresh_engine_kept(monkeypatch):
    """刚用过（未超阈值）：引擎保留，不触发重置。"""
    import time as time_mod

    from invoicing.parse.ocr import PaddleOcrProvider

    provider = PaddleOcrProvider()
    provider._engine = object()
    provider._last_used = time_mod.monotonic()  # 刚用过
    provider._ocr_uncached = lambda img: OcrText(text="x", confidence=1.0)
    result = provider.ocr_image(b"img-fresh")
    assert result is not None
    assert provider._engine is not None  # 引擎保留


def test_ocr_watchdog_timeout_resets_engine(monkeypatch):
    """调用超时（引擎卡死）→ 重置引擎并返回 None 降级，不无限阻塞。"""
    import time as time_mod

    from invoicing.parse.ocr import PaddleOcrProvider

    provider = PaddleOcrProvider()
    provider._engine = object()
    provider._last_used = time_mod.monotonic()

    def hang(img):
        time_mod.sleep(60)  # 模拟卡死（看门狗会在 timeout 前切回）
        return OcrText(text="x", confidence=1.0)

    provider._ocr_uncached = hang
    result = provider.ocr_image(b"img-watchdog", timeout_seconds=0.2)
    assert result is None
    assert provider._engine is None  # 卡死后引擎已重置
