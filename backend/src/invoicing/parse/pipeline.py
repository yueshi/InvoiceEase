"""解析策略链（设计 V0.1 §一）：按文件类型声明能力优先级，成功短路，未命中下移。

链序保证 FRD「结构化优先、LLM 仅兜底」。策略返回 None 表示未命中继续下移；
返回 ParseOutcome 则链终止（成功或带错误的失败均终止）。
"""
from typing import Callable

from invoicing.models.enums import FileType, ParseSource
from invoicing.parse.schemas import ParseError, ParseOutcome, ParsedInvoice


class ParseContext:
    """跨策略共享的惰性产物缓存——避免重复渲染/OCR（一次 OFD 只渲染一次图）。"""

    __slots__ = ("file_type", "data", "xml", "text", "image", "ocr_text")

    def __init__(self, file_type: str, data: bytes):
        self.file_type = file_type
        self.data = data
        self.xml: bytes | None = None        # 内嵌 XBRL 提取结果（惰性）
        self.text: str | None = None         # 文本层（惰性）
        self.image: bytes | None = None      # 渲染/取出的页面图（惰性）
        self.ocr_text = None                 # OCR 结果 OcrText（惰性）


Strategy = Callable[[ParseContext], ParseOutcome | None]


def _parse_structured(xml: bytes, source: ParseSource, xml_data: bytes) -> ParseOutcome:
    from invoicing.parse.validation import validate
    from invoicing.parse.xml_parser import parse_invoice_xml

    try:
        parsed = parse_invoice_xml(xml)
    except ValueError as e:
        return ParseOutcome(
            source=None, parsed=None, xml_data=xml_data,
            errors=[ParseError(code="XML_PARSE_ERROR", message=str(e))],
        )
    parsed.parse_source = source.value  # 一致性：parsed 来源与路由一致
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors, xml_data=xml_data)


def _parse_text_outcome(text: str, source: ParseSource) -> ParseOutcome | None:
    from invoicing.parse.text_rules import extract_fields_from_text
    from invoicing.parse.validation import validate

    parsed = extract_fields_from_text(text)
    if parsed is None:
        return None
    parsed.parse_source = source.value
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors)


def _parse_ocr_outcome(image_bytes: bytes, source: ParseSource) -> ParseOutcome | None:
    from invoicing.parse.ocr import get_ocr_provider
    from invoicing.parse.text_rules import extract_fields_from_text
    from invoicing.parse.validation import validate

    provider = get_ocr_provider()
    if provider is None:
        return None
    ocr_text = provider.ocr_image(image_bytes)
    if ocr_text is None:
        return None
    parsed = extract_fields_from_text(ocr_text.text, confidence=ocr_text.confidence)
    if parsed is None:
        return None
    parsed.parse_source = source.value
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors)


def strategy_structured_xml(ctx: ParseContext) -> ParseOutcome | None:
    """XML 文件整体解析（数电票/traditional/rai 变体分发）。"""
    return _parse_structured(ctx.data, ParseSource.XML, xml_data=ctx.data)


def strategy_xbrl_ofd(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.xbrl import extract_xml_from_ofd

    if ctx.xml is None:
        ctx.xml = extract_xml_from_ofd(ctx.data)
    if ctx.xml is None:
        return None
    return _parse_structured(ctx.xml, ParseSource.OFD_XBRL, xml_data=ctx.xml)


def strategy_xbrl_pdf(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.xbrl import extract_xml_from_pdf

    if ctx.xml is None:
        ctx.xml = extract_xml_from_pdf(ctx.data)
    if ctx.xml is None:
        return None
    return _parse_structured(ctx.xml, ParseSource.PDF_XBRL, xml_data=ctx.xml)


def strategy_text_ofd(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.ofd_text import extract_text_from_ofd

    if ctx.text is None:
        ctx.text = extract_text_from_ofd(ctx.data)
    if not ctx.text:
        return None
    return _parse_text_outcome(ctx.text, ParseSource.OFD_TEXT)


def strategy_text_pdf(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.pdf_text_parser import extract_pdf_text

    if ctx.text is None:
        ctx.text = extract_pdf_text(ctx.data)
    if not ctx.text:
        return None
    return _parse_text_outcome(ctx.text, ParseSource.PDF_TEXT)


def strategy_ocr_image(ctx: ParseContext) -> ParseOutcome | None:
    return _parse_ocr_outcome(ctx.data, ParseSource.IMAGE_OCR)


def strategy_ocr_pdf(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.ocr import render_pdf_first_page

    if ctx.image is None:
        ctx.image = render_pdf_first_page(ctx.data)
    if ctx.image is None:
        return None
    return _parse_ocr_outcome(ctx.image, ParseSource.PDF_OCR)


def strategy_ocr_ofd(ctx: ParseContext) -> ParseOutcome | None:
    """字体转曲 OFD：矢量渲染整页 → OCR；失败再取页面图 → OCR（与旧路由顺序一致）。"""
    from invoicing.parse.ocr import extract_ofd_page_image
    from invoicing.parse.ofd_render import render_ofd_page_to_png

    img = render_ofd_page_to_png(ctx.data)
    if img:
        outcome = _parse_ocr_outcome(img, ParseSource.OFD_OCR)
        if outcome is not None:
            ctx.image = img
            return outcome
    img = extract_ofd_page_image(ctx.data)
    if img:
        ctx.image = img
        return _parse_ocr_outcome(img, ParseSource.OFD_OCR)
    return None


CHAINS: dict[str, list[Strategy]] = {
    FileType.XML.value: [strategy_structured_xml],
    FileType.OFD.value: [strategy_xbrl_ofd, strategy_text_ofd, strategy_ocr_ofd],
    FileType.PDF.value: [strategy_xbrl_pdf, strategy_text_pdf, strategy_ocr_pdf],
    FileType.IMAGE.value: [strategy_ocr_image],
}


def parse_file(
    file_type: str,
    data: bytes,
    chains: dict[str, list[Strategy]] | None = None,
) -> ParseOutcome:
    """按文件类型的策略链解析；全部未命中 → PDF_UNSTRUCTURED（待复核）。"""
    ctx = ParseContext(file_type, data)
    for strategy in (chains or CHAINS).get(file_type, []):
        outcome = strategy(ctx)
        if outcome is not None:
            return outcome
    return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
