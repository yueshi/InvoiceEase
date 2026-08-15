from invoicing.models.enums import FileType, ParseSource
from invoicing.parse.schemas import ParseError, ParseOutcome
from invoicing.parse.validation import validate
from invoicing.parse.xbrl import extract_xml_from_ofd, extract_xml_from_pdf
from invoicing.parse.xml_parser import parse_invoice_xml


def _parse_structured(data: bytes, source: ParseSource) -> ParseOutcome:
    try:
        parsed = parse_invoice_xml(data)
    except ValueError as e:
        return ParseOutcome(source=None, parsed=None, xml_data=data, errors=[ParseError(code="XML_PARSE_ERROR", message=str(e))])
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors, xml_data=data)


def parse_file(file_type: str, data: bytes) -> ParseOutcome:
    if file_type == FileType.XML.value:
        return _parse_structured(data, ParseSource.XML)
    if file_type == FileType.OFD.value:
        xml = extract_xml_from_ofd(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.OFD_XBRL)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    if file_type == FileType.PDF.value:
        xml = extract_xml_from_pdf(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.PDF_XBRL)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    raise ValueError(f"不支持的文件类型: {file_type}")
