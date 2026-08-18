"""XML 数字签名验签测试（A1 合规：财会〔2025〕9 号）。"""
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

from invoicing.parse.xml_parser import parse_invoice_xml, verify_xml_signature

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def _make_key_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-invoice-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1))
        .not_valid_after(datetime(2030, 1, 1))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, cert_pem


def _signed_xml(inner: bytes) -> bytes:
    """用 signxml 自签一张 XML（测试 fixture 生成器）。"""
    key, cert = _make_key_cert()
    signer = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
    )
    signed = signer.sign(etree.fromstring(inner), key=key, cert=cert)
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")


def test_verify_valid_signature():
    data = _signed_xml(b"<Invoice><Number>1</Number></Invoice>")
    ok, reason = verify_xml_signature(data)
    assert ok is True, reason


def test_verify_tampered_xml_fails():
    """篡改票面内容后验签必须失败（防伪造核心场景）。"""
    data = _signed_xml(b"<Invoice><Number>1</Number></Invoice>")
    tampered = data.replace(b"<Number>1</Number>", b"<Number>999</Number>")
    ok, reason = verify_xml_signature(tampered)
    assert ok is False


def test_unsigned_xml_reports_no_signature():
    """旧格式无 Signature 节点 → no-signature（调用方按 D4 记警告不拦）。"""
    ok, reason = verify_xml_signature(b"<Invoice><Number>1</Number></Invoice>")
    assert ok is False
    assert reason == "no-signature"


def test_parse_unsigned_dianzi_fixture_still_works():
    """回归：现有无签名 fixture 解析不受影响（记 XML_NO_SIGNATURE 警告不阻断）。"""
    data = (FIXTURES / "dianzi.xml").read_bytes()
    parsed = parse_invoice_xml(data)
    assert parsed is not None
    assert parsed.invoice_number
