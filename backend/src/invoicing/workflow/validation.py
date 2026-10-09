"""v1.1 §5.2 验证服务 MCP 出参：3 类结果 + error/warning 二级。

MCP 工具 validate_expense 返回的 ValidationResult 序列化形态（dict）：
{
    "outcome": "PASS" | "FAIL" | "NEEDS_REVIEW",
    "errors":   [{"code": str, "message": str, "severity": "error"}],
    "warnings": [{"code": str, "message": str, "severity": "warning"}]
}

判定规则（v1.1 §2.x）：
- 任一 error → outcome=FAIL
- 仅 warning → outcome=NEEDS_REVIEW
- 全通过 → outcome=PASS
"""
from dataclasses import dataclass, field
from enum import Enum


class ValidationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class ValidationError:
    code: str
    message: str
    severity: str  # "error" | "warning"


@dataclass
class ValidationResult:
    outcome: ValidationOutcome
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    def is_passing(self) -> bool:
        """PASS 与 NEEDS_REVIEW 都算"可走"；FAIL 阻塞。"""
        return self.outcome in (ValidationOutcome.PASS, ValidationOutcome.NEEDS_REVIEW)