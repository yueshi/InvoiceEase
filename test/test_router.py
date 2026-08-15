from pathlib import Path

from invoicing.parse.router import parse_file

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_route_xml():
    outcome = parse_file("XML", (FIXTURES / "dianzi.xml").read_bytes())
    assert outcome.source == "XML"
    assert outcome.parsed.invoice_number == "24312000000012345678"
    assert outcome.errors == []


def test_route_pdf_unstructured():
    outcome = parse_file("PDF", b"%PDF-1.4 no attachments")
    assert outcome.source == "PDF_UNSTRUCTURED"
    assert outcome.parsed is None
    assert outcome.xml_data is None


def test_route_xml_keeps_raw_xml():
    data = (FIXTURES / "dianzi.xml").read_bytes()
    outcome = parse_file("XML", data)
    assert outcome.xml_data == data
