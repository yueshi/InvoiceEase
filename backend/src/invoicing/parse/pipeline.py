"""解析策略链（设计 V0.1 §一）：按文件类型声明能力优先级，成功短路，未命中下移。

链序保证 FRD「结构化优先、LLM 仅兜底」。策略返回 None 表示未命中继续下移；
返回 ParseOutcome 则链终止（成功或带错误的失败均终止）。
"""
import logging
from typing import Callable

from invoicing.models.enums import FileType, ParseSource
from invoicing.parse.llm import get_llm_engine  # 模块级绑定：策略统一引用此处（测试注入点）
from invoicing.parse.schemas import ParseError, ParseOutcome, ParsedInvoice

logger = logging.getLogger(__name__)


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

GATE_FIELDS = ("buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id", "total_amount")


def _needs_llm(parsed: ParsedInvoice | None, ocr_confidence: float | None) -> bool:
    """规则结果质量门（LLM 优先架构下的规则降级路径把关）：
    ① 规则提取失败 ② 5 关键字段缺 ≥2 ③ OCR 置信度 < 0.8。
    任一命中 → 规则结果不可用，继续链（不产出半成品）。

    注意：validate() 的金额矛盾类 errors 不进此判定——矛盾票直接待复核。
    """
    if parsed is None:
        return True
    missing = sum(1 for f in GATE_FIELDS if not getattr(parsed, f))
    if missing >= 2:
        return True
    if ocr_confidence is not None and ocr_confidence < 0.8:
        return True
    return False


def _llm_text_outcome(ctx: ParseContext) -> ParseOutcome | None:
    """LLM 文本通道：engine 缺失/失败 → None（继续链）。"""
    from invoicing.parse.validation import validate

    engine = get_llm_engine()
    if engine is None:
        return None
    text = ctx.ocr_text.text if ctx.ocr_text is not None else (ctx.text or "")
    if not text:
        return None
    parsed = engine.extract_from_text(text)
    if parsed is None:
        return None
    errors = validate(parsed)
    return ParseOutcome(source=ParseSource.LLM_TEXT.value, parsed=parsed, errors=errors)


def _parse_structured(xml: bytes, source: ParseSource, xml_data: bytes) -> ParseOutcome:
    from invoicing.parse.validation import validate
    from invoicing.parse.xml_parser import parse_invoice_xml, verify_xml_signature

    # A1 合规：XML 含 Signature 必须验签通过；无签名旧格式票记警告不拦（D4）
    sig_ok, sig_reason = verify_xml_signature(xml)
    if not sig_ok and sig_reason != "no-signature":
        return ParseOutcome(
            source=None, parsed=None, xml_data=xml_data,
            errors=[ParseError(code="XML_SIGNATURE_INVALID", message=f"数字签名验证失败: {sig_reason}")],
        )
    if not sig_ok:
        logger.warning("XML 原件无数字签名（旧格式），仅记警告不拦截")

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


def _text_strategy(ctx: ParseContext, extractor, source: ParseSource) -> ParseOutcome | None:
    """文本层策略：LLM 文本通道优先（FRD 原意：版式 PDF 走 OCR+大模型）——
    不可用/失败时降级文本规则；规则结果经质量门（缺 ≥2 字段）把关，不产半成品。"""
    if ctx.text is None:
        ctx.text = extractor(ctx.data)
    if not ctx.text:
        return None
    # 1. LLM 优先
    llm_outcome = _llm_text_outcome(ctx)
    if llm_outcome is not None:
        return llm_outcome
    # 2. 规则降级
    from invoicing.parse.text_rules import extract_fields_from_text
    from invoicing.parse.validation import validate

    parsed = extract_fields_from_text(ctx.text)
    if parsed is None or _needs_llm(parsed, None):
        return None  # 规则也失败/质量不过 → 继续链（VLM/待复核）
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

    return _text_strategy(ctx, extract_text_from_ofd, ParseSource.OFD_TEXT)


def strategy_text_pdf(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.pdf_text_parser import extract_pdf_text

    return _text_strategy(ctx, extract_pdf_text, ParseSource.PDF_TEXT)


def _ocr_strategy(ctx: ParseContext, image: bytes, source: ParseSource) -> ParseOutcome | None:
    """OCR 策略：OCR → LLM 文本通道优先 → 规则降级（质量门把关，不产半成品）。"""
    from invoicing.parse.ocr import get_ocr_provider
    from invoicing.parse.text_rules import extract_fields_from_text
    from invoicing.parse.validation import validate

    provider = get_ocr_provider()
    if provider is None:
        return None
    ocr_text = provider.ocr_image(image)
    if ocr_text is None:
        return None
    ctx.ocr_text = ocr_text
    # 1. LLM 优先
    llm_outcome = _llm_text_outcome(ctx)
    if llm_outcome is not None:
        return llm_outcome
    # 2. 规则降级
    parsed = extract_fields_from_text(ocr_text.text, confidence=ocr_text.confidence)
    if parsed is None or _needs_llm(parsed, ocr_text.confidence):
        return None  # 规则也失败/质量不过 → 继续链
    parsed.parse_source = source.value
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors)


def strategy_ocr_image(ctx: ParseContext) -> ParseOutcome | None:
    return _ocr_strategy(ctx, ctx.data, ParseSource.IMAGE_OCR)


def strategy_ocr_pdf(ctx: ParseContext) -> ParseOutcome | None:
    from invoicing.parse.ocr import render_pdf_first_page

    if ctx.image is None:
        ctx.image = render_pdf_first_page(ctx.data)
    if ctx.image is None:
        return None
    return _ocr_strategy(ctx, ctx.image, ParseSource.PDF_OCR)


def strategy_ocr_ofd(ctx: ParseContext) -> ParseOutcome | None:
    """字体转曲 OFD：矢量渲染整页 → OCR；失败再取页面图 → OCR（与旧路由顺序一致）。"""
    from invoicing.parse.ocr import extract_ofd_page_image
    from invoicing.parse.ofd_render import render_ofd_page_to_png

    img = render_ofd_page_to_png(ctx.data)
    if img:
        ctx.image = img
        outcome = _ocr_strategy(ctx, img, ParseSource.OFD_OCR)
        if outcome is not None:
            return outcome
    img = extract_ofd_page_image(ctx.data)
    if img:
        ctx.image = img
        return _ocr_strategy(ctx, img, ParseSource.OFD_OCR)
    return None


def strategy_llm_vlm(ctx: ParseContext) -> ParseOutcome | None:
    """VLM 兜底（设计 §3.2）：直接看图提取；engine 缺失/失败 → None。"""
    from invoicing.parse.validation import validate

    engine = get_llm_engine()
    if engine is None:
        return None
    image = ctx.image if ctx.image is not None else ctx.data  # IMAGE 文件本身即图
    parsed = engine.extract_from_image(image)
    if parsed is None:
        return None
    errors = validate(parsed)
    return ParseOutcome(source=ParseSource.VLM.value, parsed=parsed, errors=errors)


CHAINS: dict[str, list[Strategy]] = {
    FileType.XML.value: [strategy_structured_xml],
    FileType.OFD.value: [strategy_xbrl_ofd, strategy_text_ofd, strategy_ocr_ofd, strategy_llm_vlm],
    FileType.PDF.value: [strategy_xbrl_pdf, strategy_text_pdf, strategy_ocr_pdf, strategy_llm_vlm],
    FileType.IMAGE.value: [strategy_ocr_image, strategy_llm_vlm],
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
