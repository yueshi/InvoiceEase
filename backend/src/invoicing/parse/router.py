from invoicing.models.enums import FileType, ParseSource
from invoicing.parse.schemas import ParseError, ParseOutcome
from invoicing.parse.validation import validate
from invoicing.parse.xml_parser import parse_invoice_xml


def _parse_structured(data: bytes, source: ParseSource) -> ParseOutcome:
    try:
        parsed = parse_invoice_xml(data)
    except ValueError as e:
        return ParseOutcome(source=None, parsed=None, xml_data=data, errors=[ParseError(code="XML_PARSE_ERROR", message=str(e))])
    parsed.parse_source = source.value  # 一致性：parsed 来源与路由一致
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors, xml_data=data)


def _parse_text(text: str, source: ParseSource) -> ParseOutcome:
    from invoicing.parse.text_rules import extract_fields_from_text

    parsed = extract_fields_from_text(text)
    if parsed is None:
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    parsed.parse_source = source.value
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors)


def parse_file(file_type: str, data: bytes) -> ParseOutcome:
    """三级路由：内嵌结构化 XML → 文本层规则提取 → PDF_UNSTRUCTURED（待复核）。"""
    if file_type == FileType.XML.value:
        return _parse_structured(data, ParseSource.XML)
    if file_type == FileType.OFD.value:
        from invoicing.parse.ofd_text import extract_text_from_ofd
        from invoicing.parse.xbrl import extract_xml_from_ofd

        xml = extract_xml_from_ofd(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.OFD_XBRL)
        text = extract_text_from_ofd(data)
        if text:
            return _parse_text(text, ParseSource.OFD_TEXT)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    if file_type == FileType.PDF.value:
        from invoicing.parse.pdf_text_parser import extract_pdf_text
        from invoicing.parse.xbrl import extract_xml_from_pdf

        xml = extract_xml_from_pdf(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.PDF_XBRL)
        text = extract_pdf_text(data)
        if text:
            return _parse_text(text, ParseSource.PDF_TEXT)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    raise ValueError(f"不支持的文件类型: {file_type}")
