import io
import zipfile

from pypdf import PdfReader

from invoicing.parse.xml_parser import parse_invoice_xml


def _is_invoice_xml(data: bytes) -> bool:
    try:
        parse_invoice_xml(data)
        return True
    except ValueError:
        return False


def _find_invoice_xml(candidates: list[bytes]) -> bytes | None:
    for data in candidates:
        if _is_invoice_xml(data):
            return data
    return None


def extract_xml_from_ofd(data: bytes) -> bytes | None:
    """OFD 是 zip 容器，遍历其中 XML 文件，返回第一个可解析为数电票的 XML 原文。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        candidates = [
            zf.read(name)
            for name in zf.namelist()
            if name.lower().endswith(".xml") and not name.endswith("/")
        ]
    except zipfile.BadZipFile:
        return None
    return _find_invoice_xml(candidates)


def extract_xml_from_pdf(data: bytes) -> bytes | None:
    """提取 PDF 内嵌附件中的数电票 XML。"""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return None
    candidates: list[bytes] = []
    attachments = getattr(reader, "attachments", None) or {}
    for payload in attachments.values():
        if isinstance(payload, (list, tuple)):
            candidates.extend(p for p in payload if isinstance(p, bytes))
        elif isinstance(payload, bytes):
            candidates.append(payload)
    return _find_invoice_xml(candidates)
