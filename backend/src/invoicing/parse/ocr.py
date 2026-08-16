"""OCR 引擎抽象：本地 PaddleOCR（惰性初始化），缺 optional 依赖时优雅降级。

安装：`uv sync --extra ocr`（paddlepaddle/paddleocr/pymupdf）。
未安装时 get_ocr_provider() 返回 None，路由落待复核，服务不故障。
"""
import io
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OcrText:
    text: str
    confidence: float  # 各行识别置信度均值 0-1


class OcrProvider(ABC):
    @abstractmethod
    def ocr_image(self, image_bytes: bytes) -> OcrText | None: ...


class PaddleOcrProvider(OcrProvider):
    def __init__(self) -> None:
        self._engine = None
        self._init_failed = False

    def _ensure_engine(self):
        if self._engine is None and not self._init_failed:
            try:
                from paddleocr import PaddleOCR  # 惰性导入：optional 依赖

                self._engine = PaddleOCR(lang="ch", use_angle_cls=True)  # 角度分类提升字形识别
            except Exception:
                self._init_failed = True
        return self._engine

    def ocr_image(self, image_bytes: bytes) -> OcrText | None:
        engine = self._ensure_engine()
        if engine is None:
            return None
        try:
            import numpy as np
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            max_side = max(img.size)
            if max_side > 2400:
                scale = 2400 / max_side
                img = img.resize((int(img.width * scale), int(img.height * scale)))
            img = np.array(img)
            result = engine.predict(img) if hasattr(engine, "predict") else engine.ocr(img)
        except Exception:
            return None
        texts, scores = [], []
        if hasattr(engine, "predict"):
            # paddleocr 3.x：list[dict]，rec_texts/rec_scores
            for page in result:
                texts.extend(page.get("rec_texts") or [])
                scores.extend(page.get("rec_scores") or [])
        else:
            # paddleocr 2.x：list[list[[box, (text, score)], ...]]
            for page in result:
                if not page:
                    continue
                for item in page:
                    text, score = item[1]
                    texts.append(text)
                    scores.append(score)
        if not texts:
            return None
        conf = sum(float(s) for s in scores) / len(scores) if scores else 0.0
        return OcrText(text="\n".join(texts), confidence=conf)


_provider: OcrProvider | None = None
_tried = False


def preload_ocr_engine() -> None:
    """后台预热：模型加载（首次 10-30s）移出请求路径，避免首次调用超时。"""
    import threading

    def _warm() -> None:
        try:
            provider = get_ocr_provider()
            if provider is not None:
                provider.ocr_image(b"")  # 触发引擎初始化（空输入返回 None，无副作用）
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True).start()


def get_ocr_provider() -> OcrProvider | None:
    global _provider, _tried
    if _provider is None and not _tried:
        try:
            import paddleocr  # noqa: F401

            _provider = PaddleOcrProvider()
        except ImportError:
            _provider = None
        _tried = True
    return _provider


def render_pdf_first_page(pdf_bytes: bytes) -> bytes | None:
    """扫描版 PDF 首页渲染为 PNG（200dpi）；pymupdf 缺失或渲染失败返回 None。"""
    try:
        import fitz  # pymupdf

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count < 1:
            return None
        pix = doc[0].get_pixmap(dpi=200)
        return pix.tobytes("png")
    except Exception:
        return None


def extract_ofd_page_image(ofd_bytes: bytes) -> bytes | None:
    """粘连文本 OFD：取 Res 目录下最大的页面图片（近似主版面）。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(ofd_bytes))
    except zipfile.BadZipFile:
        return None
    best = None
    for name in zf.namelist():
        if name.lower().endswith(".png"):
            data = zf.read(name)
            if best is None or len(data) > len(best):
                best = data
    return best
