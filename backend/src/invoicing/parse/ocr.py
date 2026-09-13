"""OCR 引擎抽象：本地 PaddleOCR（惰性初始化），缺 optional 依赖时优雅降级。

安装：`uv sync --extra ocr`（paddlepaddle/paddleocr/pymupdf）。
未安装时 get_ocr_provider() 返回 None，路由落待复核，服务不故障。
"""
import io
import logging
import threading
import time
import zipfile
from hashlib import sha1
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OcrText:
    text: str
    confidence: float  # 各行识别置信度均值 0-1


class OcrProvider(ABC):
    @abstractmethod
    def ocr_image(self, image_bytes: bytes) -> OcrText | None: ...


_cache: dict[str, OcrText] = {}
_CACHE_MAX = 64
# 闲置超阈值丢弃引擎：长时间空闲后 paddle 推理易卡死（内存换页/线程池回收），重载慢但确定
_IDLE_RESET_SECONDS = 30 * 60
# 单次 OCR 调用看门狗：超时判定引擎卡死 → 重置 + 本单降级返回 None，不无限阻塞服务
_OCR_TIMEOUT_SECONDS = 90.0


class PaddleOcrProvider(OcrProvider):
    def __init__(self) -> None:
        self._engine = None
        self._init_failed = False
        self._last_used = 0.0  # time.monotonic：引擎上次成功调用时间

    def _ensure_engine(self):
        if self._engine is None and not self._init_failed:
            try:
                from paddleocr import PaddleOCR  # 惰性导入：optional 依赖

                # 注：enable_mkldnn 在本机 paddle 2.6 wheel 上缺 API 不可用，勿开启
                self._engine = PaddleOCR(lang="ch", use_angle_cls=True)
            except Exception:
                self._init_failed = True
        return self._engine

    def ocr_image(
        self, image_bytes: bytes, timeout_seconds: float = _OCR_TIMEOUT_SECONDS
    ) -> OcrText | None:
        # 结果缓存：同一图片（如 WorkBuddy 对同一文件重复调用）秒回
        key = sha1(image_bytes).hexdigest()
        if key in _cache:
            return _cache[key]
        # 闲置重置：距上次调用超阈值 → 丢弃引擎，下次 _ensure_engine 重新加载
        # （长时间空闲后 paddle 推理易卡死：内存换页/线程池回收；重载慢但确定）
        if self._engine is not None and time.monotonic() - self._last_used > _IDLE_RESET_SECONDS:
            self._engine = None
        result = self._ocr_with_timeout(image_bytes, timeout_seconds)
        if result is not None:
            self._last_used = time.monotonic()
            if len(_cache) >= _CACHE_MAX:
                _cache.pop(next(iter(_cache)))
            _cache[key] = result
        return result

    def _ocr_with_timeout(self, image_bytes: bytes, timeout_seconds: float) -> OcrText | None:
        """看门狗：OCR 在独立线程执行；超时未完成 → 判定引擎卡死，重置并本单降级。"""
        holder: dict = {}

        def _run() -> None:
            try:
                holder["result"] = self._ocr_uncached(image_bytes)
            except Exception:
                holder["result"] = None

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout_seconds)
        if worker.is_alive():
            logger.warning(
                "OCR 调用超时（%.0fs），判定引擎卡死：重置引擎，本单降级走待复核", timeout_seconds
            )
            self._engine = None
            self._init_failed = False
            return None
        return holder.get("result")

    def _ocr_uncached(self, image_bytes: bytes) -> OcrText | None:
        engine = self._ensure_engine()
        if engine is None:
            return None
        try:
            import numpy as np
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            max_side = max(img.size)
            if max_side > 1800:
                scale = 1800 / max_side
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


def preload_ocr_engine(background: bool = True) -> None:
    """预热 OCR 引擎：模型加载（首次 10-30s）移出请求路径，避免首次调用超时。

    **同步**判定引擎是否在位：未安装时直接返回——既不建线程也不碰 provider 单例，
    因此服务启动（及测试里反复 create_app）不会因它产生任何副作用。
    background=False 时同步阻塞预热（服务启动变慢约 25s，但首个 OCR 请求即热引擎）。
    """
    provider = get_ocr_provider()
    if provider is None:
        return

    def _warm() -> None:
        try:
            provider.ocr_image(b"")  # 触发引擎初始化（空输入返回 None，无副作用）
        except Exception:
            pass

    if background:
        threading.Thread(target=_warm, daemon=True).start()
    else:
        _warm()


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
