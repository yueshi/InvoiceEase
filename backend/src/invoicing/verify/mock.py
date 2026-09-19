import json

from invoicing.config import settings
from invoicing.models import Invoice
from invoicing.verify.provider import VerifyProvider, VerifyResult


class MockVerifyProvider(VerifyProvider):
    """规则引擎模拟验真：发票号码前缀命中 fail_prefixes 判失败、error_prefixes 判异常，否则通过。"""

    # 结构化模拟标志：验真落库时写入 verify_detail["mock"]，披露判定不依赖 reason 文案
    is_mock = True

    def __init__(self) -> None:
        rules = json.loads(settings.mock_verify_rules)
        self.fail_prefixes = rules.get("fail_prefixes", [])
        self.error_prefixes = rules.get("error_prefixes", [])

    def verify(self, invoice: Invoice) -> VerifyResult:
        number = invoice.invoice_number or ""
        for prefix in self.error_prefixes:
            if number.startswith(prefix):
                return VerifyResult(
                    status="error", detail={"reason": "mock_rule_error_prefix"}, raw={}
                )
        for prefix in self.fail_prefixes:
            if number.startswith(prefix):
                return VerifyResult(
                    status="failed", detail={"reason": "mock_rule_fail_prefix"}, raw={}
                )
        return VerifyResult(status="passed", detail={"reason": "mock_rule_default_pass"}, raw={})
