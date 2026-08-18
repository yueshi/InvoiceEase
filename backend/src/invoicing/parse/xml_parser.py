from datetime import date
from decimal import Decimal

from lxml import etree

from invoicing.models.enums import ParseSource
from invoicing.parse.schemas import ParsedInvoice


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def verify_xml_signature(data: bytes) -> tuple[bool, str | None]:
    """验签 XMLDSig（A1 合规，财会〔2025〕9 号）。返回 (是否通过, 失败原因)：

    无 Signature 节点 → (False, "no-signature")（旧格式票，调用方记警告不拦，D4）。
    信任模型：P2 用签名内嵌证书验完整性（防篡改）；税务 CA 链验证待 M10
    真实验真资质落地后补充。失败原因不含原始 XML（防日志泄漏票面数据）。
    """
    from signxml import XMLVerifier

    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return False, "xml-parse-error"
    ds = "{http://www.w3.org/2000/09/xmldsig#}"
    sig = root.find(f".//{ds}Signature")
    if sig is None:
        return False, "no-signature"
    # 提取票内声明证书作为信任锚：P2 信任模型=验完整性防篡改（签名必须由票内
    # 密钥正确签署）；税务 CA 链验证待 M10 真实验真资质落地后补充
    x509_cert = None
    cert_el = sig.find(f".//{ds}X509Certificate")
    if cert_el is not None and cert_el.text:
        import base64

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        try:
            der = base64.b64decode(cert_el.text.strip())
            x509_cert = x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM)
        except Exception:
            x509_cert = None
    try:
        XMLVerifier().verify(data, x509_cert=x509_cert)
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


def _first_text(root, *names: str) -> str | None:
    wanted = set(names)
    for el in root.iter():
        if _local_name(el.tag) in wanted and el.text is not None:
            return el.text.strip()
    return None


def parse_invoice_xml(data: bytes) -> ParsedInvoice:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"XML_PARSE_ERROR: {e}")

    # 变体分流（数电票 schema 为主，变体按结构判别）：
    # 传统增值税电子普通发票（EInvoiceData）→ 铁路客票 rai XBRL → 数电票
    from invoicing.parse.rai_parser import is_rai_xbrl, parse_rai_xbrl
    from invoicing.parse.traditional_parser import is_traditional_einvoice, parse_traditional_einvoice

    if is_traditional_einvoice(root):
        return parse_traditional_einvoice(root)
    if is_rai_xbrl(root):
        return parse_rai_xbrl(root)

    number = _first_text(root, "EInvoiceNumber", "InvoiceNumber")
    issue_date = _first_text(root, "IssueDate")
    if not number or not issue_date:
        raise ValueError(
            "XML_PARSE_ERROR: 缺少 EInvoiceNumber/IssueDate，不是数电票 XML"
        )
    try:
        parsed_date = date.fromisoformat(issue_date)
    except ValueError as e:
        raise ValueError(f"XML_PARSE_ERROR: 开票日期格式非法 {issue_date}")

    return ParsedInvoice(
        invoice_number=number,
        issue_date=parsed_date,
        amount_without_tax=Decimal(_first_text(root, "TotalAmountExcludingTax") or "0"),
        tax_amount=Decimal(_first_text(root, "TotalTaxAmount") or "0"),
        total_amount=Decimal(_first_text(root, "AmountInFigures") or "0"),
        total_amount_cn=_first_text(root, "AmountInWords") or "",
        seller_name=_first_text(root, "SellerName") or "",
        seller_tax_id=_first_text(root, "SellerTaxpayerIdentificationNumber") or "",
        buyer_name=_first_text(root, "BuyerName") or "",
        buyer_tax_id=_first_text(root, "BuyerTaxpayerIdentificationNumber") or "",
        invoice_type=_first_text(root, "EInvoiceType"),
        confidence_score=1.0,
        parse_source=ParseSource.XML.value,
    )
