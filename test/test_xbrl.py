import io
import zipfile
from pathlib import Path

from pypdf import PdfWriter

from invoicing.parse.xbrl import extract_xml_from_ofd, extract_xml_from_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
INVOICE_XML = (FIXTURES / "dianzi.xml").read_bytes()


def _build_ofd_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org'/>")
        zf.writestr("Doc_0/Attachs/original_invoice.xml", INVOICE_XML)
    return buf.getvalue()


def _build_pdf_with_attachment() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_attachment("original_invoice.xml", INVOICE_XML)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _build_plain_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_xml_from_ofd():
    assert extract_xml_from_ofd(_build_ofd_bytes()) is not None


def test_extract_xml_from_ofd_returns_none_for_plain_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    assert extract_xml_from_ofd(buf.getvalue()) is None


def test_extract_xml_from_pdf_attachment():
    xml = extract_xml_from_pdf(_build_pdf_with_attachment())
    assert xml is not None


def test_extract_xml_from_pdf_plain_returns_none():
    assert extract_xml_from_pdf(_build_plain_pdf()) is None


def test_extracted_xml_is_parseable():
    from invoicing.parse.xml_parser import parse_invoice_xml

    parsed = parse_invoice_xml(extract_xml_from_ofd(_build_ofd_bytes()))
    assert parsed.invoice_number == "24312000000012345678"
