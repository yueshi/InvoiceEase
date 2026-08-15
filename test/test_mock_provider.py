from decimal import Decimal

from invoicing.models import Invoice
from invoicing.verify.mock import MockVerifyProvider
from invoicing.verify.provider import get_provider, VerifyResult


def test_mock_pass_by_default():
    inv = Invoice(invoice_number="24312000000012345678", file_url="a.xml", file_type="XML")
    result = MockVerifyProvider().verify(inv)
    assert isinstance(result, VerifyResult)
    assert result.status == "passed"


def test_mock_fail_prefix():
    inv = Invoice(invoice_number="00001234567890123456", file_url="a.xml", file_type="XML")
    result = MockVerifyProvider().verify(inv)
    assert result.status == "failed"
    assert "reason" in result.detail


def test_mock_error_prefix():
    inv = Invoice(invoice_number="00011234567890123456", file_url="a.xml", file_type="XML")
    result = MockVerifyProvider().verify(inv)
    assert result.status == "error"


def test_get_provider_returns_mock():
    assert isinstance(get_provider(), MockVerifyProvider)
