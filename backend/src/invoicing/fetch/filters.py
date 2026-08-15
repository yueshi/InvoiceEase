import io
import zipfile

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic"}


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def classify_attachment(filename: str, content_type: str, data: bytes) -> str:
    ext = _ext(filename)
    if content_type.startswith("image/") or ext in IMAGE_EXTS:
        return "IMAGE"
    if ext == ".ofd" or content_type in ("application/ofd", "application/vnd.ofd"):
        return "OFD"
    if ext == ".zip" or content_type in ("application/zip", "application/x-zip-compressed"):
        return "ZIP"
    if ext == ".xml" or content_type in ("application/xml", "text/xml") or data.lstrip().startswith(b"<?xml"):
        return "XML"
    if ext == ".pdf" or content_type == "application/pdf" or data.startswith(b"%PDF"):
        return "PDF"
    return "OTHER"


def unpack_zip(data: bytes) -> list[tuple[str, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return []
    out = []
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        try:
            out.append((name.split("/")[-1], zf.read(name)))
        except Exception:
            continue
    return out
