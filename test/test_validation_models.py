"""Validation 数据类测试（v1.1 §5.2 验证服务 MCP 出参）。"""
from invoicing.workflow.validation import (
    ValidationOutcome,
    ValidationError,
    ValidationResult,
)


def test_validation_outcome_enum_values():
    assert ValidationOutcome.PASS.value == "PASS"
    assert ValidationOutcome.FAIL.value == "FAIL"
    assert ValidationOutcome.NEEDS_REVIEW.value == "NEEDS_REVIEW"


def test_validation_error_fields():
    e = ValidationError(code="AMOUNT_TOO_LARGE", message="金额 8000 超阈值", severity="error")
    assert e.code == "AMOUNT_TOO_LARGE"
    assert e.message == "金额 8000 超阈值"
    assert e.severity == "error"


def test_validation_result_pass_is_passing():
    r = ValidationResult(outcome=ValidationOutcome.PASS, errors=[], warnings=[])
    assert r.is_passing() is True


def test_validation_result_fail_is_not_passing():
    r = ValidationResult(
        outcome=ValidationOutcome.FAIL,
        errors=[ValidationError(code="X", message="y", severity="error")],
        warnings=[],
    )
    assert r.is_passing() is False


def test_validation_result_needs_review_is_passing():
    """NEEDS_REVIEW 也算「可走」——只是带 warning，不阻塞（spec §4.2 边界）。"""
    r = ValidationResult(
        outcome=ValidationOutcome.NEEDS_REVIEW,
        errors=[],
        warnings=[ValidationError(code="W", message="w", severity="warning")],
    )
    assert r.outcome == ValidationOutcome.NEEDS_REVIEW
    assert r.is_passing() is True


def test_validation_result_serializable_dict_shape():
    """MCP 出参需要 dict 形态（参考 validation_errors 字段约定）。"""
    r = ValidationResult(
        outcome=ValidationOutcome.FAIL,
        errors=[ValidationError(code="A", message="m1", severity="error")],
        warnings=[ValidationError(code="B", message="m2", severity="warning")],
    )
    out = {
        "outcome": r.outcome.value,
        "errors": [{"code": e.code, "message": e.message, "severity": e.severity}
                    for e in r.errors],
        "warnings": [{"code": e.code, "message": e.message, "severity": e.severity}
                      for e in r.warnings],
    }
    assert out["outcome"] == "FAIL"
    assert out["errors"] == [{"code": "A", "message": "m1", "severity": "error"}]
    assert out["warnings"] == [{"code": "B", "message": "m2", "severity": "warning"}]