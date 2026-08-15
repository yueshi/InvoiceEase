"""版式 OFD 文本提取：Pages/*/Content.xml 的 TextCode 按坐标 (Y, X) 排序拼接。"""
import io
import re
import zipfile

from lxml import etree

_PAGE_RE = re.compile(r"Pages/Page_\d+/Content\.xml$")


def extract_text_from_ofd(data: bytes) -> str | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    spans: list[tuple[float, float, str]] = []
    for name in zf.namelist():
        if not _PAGE_RE.search(name):
            continue
        try:
            root = etree.fromstring(zf.read(name))
        except Exception:
            continue
        for code in root.iter():
            if code.tag.rsplit("}", 1)[-1] != "TextCode":
                continue
            text = "".join(code.itertext()).strip()
            if not text:
                continue
            try:
                y = float(code.get("Y") or 0)
                x = float(code.get("X") or 0)
            except ValueError:
                continue
            spans.append((y, x, text))
    if not spans:
        return None
    spans.sort(key=lambda s: (s[0], s[1]))
    # 同 Y 拼一行，不同 Y 换行（近似阅读顺序）
    lines: list[str] = []
    current_y = None
    for y, _x, text in spans:
        if current_y is not None and abs(y - current_y) > 1e-6:
            lines.append("\n")
        lines.append(text)
        current_y = y
    return "".join(lines).strip() or None
