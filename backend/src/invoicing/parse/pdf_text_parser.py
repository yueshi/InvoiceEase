"""版式 PDF 文本层提取（pypdf extract_text）+ 逐页坐标（原件定位用）。"""
import io
import re

from pypdf import PdfReader


def extract_pdf_pages(data: bytes) -> list[str]:
    """逐页文本（页码 = 下标+1）；解析失败返回空列表。"""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return []
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def extract_pdf_text(data: bytes) -> str | None:
    parts = [p for p in extract_pdf_pages(data) if p]
    text = "\n".join(parts).strip()
    return text or None


class RenderUnavailable(Exception):
    """渲染依赖缺失（pypdfium2/Pillow 未安装）——调用方降级为「打开原 PDF 第 N 页」。"""


def render_pdf_page_png(data: bytes, page_no: int, dpi: int = 150) -> bytes:
    """指定页渲染为 PNG（pypdfium2 + Pillow）；依赖缺失抛 RenderUnavailable。"""
    try:
        import pypdfium2 as pdfium
    except Exception as e:  # pragma: no cover - 依赖存在时不可达
        raise RenderUnavailable(f"pypdfium2 不可用: {e}") from e
    try:
        doc = pdfium.PdfDocument(io.BytesIO(data))
        if page_no < 1 or page_no > len(doc):
            raise RenderUnavailable(f"页码越界: {page_no}/{len(doc)}")
        bitmap = doc[page_no - 1].render(scale=dpi / 72.0)
        img = bitmap.to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except RenderUnavailable:
        raise
    except Exception as e:
        raise RenderUnavailable(f"渲染失败: {e}") from e


class PdfiumLocator:
    """基于 pypdfium2 的文本定位器：PDFium 为**每个字符**重建坐标框
    （pypdf 的矩阵法在本类回单上仅覆盖约半数片段），定位覆盖率显著更高。

    用法：`loc = PdfiumLocator(data)`；`loc.locate(page_no, "1,600.00")` →
    `(y_bottom, y_top)`（PDF 坐标，原点左下）或 None。逐页缓存 textpage，
    库缺失/文档打不开时 `available=False`，调用方自然降级。
    """

    def __init__(self, data: bytes):
        self.available = False
        self._doc = None
        self._pages: dict[int, tuple[str, object, float, float]] = {}
        try:
            import pypdfium2 as pdfium

            self._doc = pdfium.PdfDocument(io.BytesIO(data))
            self.available = True
        except Exception:
            self.available = False

    def _page(self, page_no: int):
        if page_no in self._pages:
            return self._pages[page_no]
        page = self._doc[page_no - 1]
        tp = page.get_textpage()
        n = tp.count_chars()
        text = tp.get_text_range(0, n)
        w, h = page.get_size()
        self._pages[page_no] = (text, tp, float(w), float(h))
        return self._pages[page_no]

    @staticmethod
    def _find_bounded(text: str, token: str) -> int:
        """查找 token 且**词边界成立**（前后不得紧邻字母数字）。

        防误配：短数字串（如日期 "20260321"）会嵌在流水号等长数字内部，
        无边界校验会定位到无关位置。
        """
        start = 0
        while True:
            i = text.find(token, start)
            if i < 0:
                return -1
            before = text[i - 1] if i > 0 else ""
            after = text[i + len(token)] if i + len(token) < len(text) else ""
            ok_before = not before or not re.match(r"[0-9A-Za-z]", before) or not re.match(
                r"[0-9A-Za-z]", token[0]
            )
            ok_after = not after or not re.match(r"[0-9A-Za-z]", after) or not re.match(
                r"[0-9A-Za-z]", token[-1]
            )
            if ok_before and ok_after:
                return i
            start = i + 1

    def logical_text(self, page_no: int) -> str | None:
        """坐标法版面还原后的逻辑文本（竖排标签并入值行）；不可用时返回 None。"""
        if not self.available:
            return None
        try:
            text, tp, _w, _h = self._page(page_no)
        except Exception:
            return None
        from invoicing.parse.receipt_layout import reconstruct_lines

        chars: list[tuple[str, float, float]] = []
        for i in range(tp.count_chars()):
            ch = text[i]
            if ch.strip() == "":
                continue
            try:
                box = tp.get_charbox(i)
            except Exception:
                continue
            chars.append((ch, float(box[0]), float(box[1])))
        if not chars:
            return None
        lines = reconstruct_lines(chars)
        return "\n".join(lines) if lines else None

    def page_height(self, page_no: int) -> float | None:
        try:
            return self._page(page_no)[3]
        except Exception:
            return None

    def find_all(self, page_no: int, token: str) -> list[float]:
        """页内所有匹配的 y（首字符框底边），按 y 降序。

        用于**结构边界**切分：免责声明行（块尾）/回单头每张回单各一次，
        比"锚点行"位置固定得多。
        """
        if not self.available or not token:
            return []
        try:
            text, tp, _w, _h = self._page(page_no)
        except Exception:
            return []
        ys: list[float] = []
        start = 0
        while True:
            i = text.find(token, start)
            if i < 0:
                break
            try:
                ys.append(float(tp.get_charbox(i)[1]))
            except Exception:
                pass
            start = i + len(token)
        return sorted(set(ys), reverse=True)

    def locate(self, page_no: int, token: str) -> tuple[float, float] | None:
        """在指定页搜索锚点串 → 命中字符的坐标框并集 (y0, y1)。"""
        if not self.available or not token:
            return None
        try:
            text, tp, _w, _h = self._page(page_no)
        except Exception:
            return None
        idx = self._find_bounded(text, token)
        if idx < 0:
            # 归一化兜底：去掉分隔符再找（金额/串号格式差异）
            norm_text = re.sub(r"[^0-9A-Za-z]", "", text)
            norm_tok = re.sub(r"[^0-9A-Za-z]", "", token)
            if not norm_tok:
                return None
            nidx = norm_text.find(norm_tok)
            if nidx < 0:
                return None
            # 归一化下标 → 原文本下标
            count = 0
            idx = -1
            for i, ch in enumerate(text):
                if re.match(r"[0-9A-Za-z]", ch):
                    if count == nidx:
                        idx = i
                        break
                    count += 1
            if idx < 0:
                return None
        try:
            boxes = [tp.get_charbox(i) for i in range(idx, min(idx + len(token), tp.count_chars()))]
        except Exception:
            return None
        if not boxes:
            return None
        y0 = min(b[1] for b in boxes)
        y1 = max(b[3] for b in boxes)
        return (float(y0), float(y1))


def extract_pdf_page_spans(data: bytes) -> list[dict | None]:
    """逐页文本片段坐标：`[{"w","h","spans":[(text,x,y),…]} | None, …]`。

    坐标来自文本矩阵与当前变换矩阵的平移量之和（pypdf visitor）；部分片段
    （竖排/特殊绘制）矩阵为零 → 不产出坐标，定位时自然降级。
    """
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return []
    pages: list[dict | None] = []
    for page in reader.pages:
        spans: list[tuple[str, float, float]] = []

        def visitor(text, cm, tm, font_dict, font_size, _spans=spans):
            t = (text or "").strip()
            if not t:
                return
            x = cm[4] + tm[4]
            y = cm[5] + tm[5]
            if x or y:  # 零矩阵片段无位置信息
                _spans.append((t, float(x), float(y)))

        try:
            page.extract_text(visitor_text=visitor)
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
        except Exception:
            pages.append(None)
            continue
        if not spans:
            pages.append(None)
            continue
        pages.append({"w": w, "h": h, "spans": spans})
    return pages
