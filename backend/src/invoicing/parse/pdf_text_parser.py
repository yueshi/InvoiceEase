"""版式 PDF 文本层提取（pypdf extract_text）。"""
import io

from pypdf import PdfReader


def extract_pdf_text(data: bytes) -> str | None:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return None
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    text = "\n".join(parts).strip()
    return text or None
