"""WorkBuddy 文件级识别：读本地文件 → 分级解析 → ExtractResult（集成设计 V0.1 §三）。

图片按合规拒收（FRD G-03 仅收原件）；纯版式 PDF 无内嵌结构化数据时
success=False 并提示 OCR 引擎 Phase 2 支持。
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from invoicing.config import settings
from invoicing.fetch.filters import classify_attachment
from invoicing.models.enums import FileType
from invoicing.parse.router import parse_file
from invoicing.parse.schemas import ParsedInvoice
from invoicing.parse.validation import validate as core_validate
from invoicing.schemas.mcp_extract import (
    ExtractInvoiceData,
    ExtractResult,
    PartyInfo,
    ValidationResult,
)

IMAGE_REJECT = "合规拒收：仅接受 PDF/OFD/XML 原件（财会〔2025〕9 号）"
UNSTRUCTURED = "未内嵌结构化数据，OCR 引擎 Phase 2 支持"

_SUPPORTED = (FileType.PDF.value, FileType.OFD.value, FileType.XML.value)


def _party(value) -> dict:
    return value if isinstance(value, dict) else {}


def _read_file(file_path: str) -> bytes:
    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"文件不存在: {file_path}")
    if settings.workbuddy_inbox_dir:
        root = Path(settings.workbuddy_inbox_dir).resolve()
        if root not in path.resolve().parents and path.resolve() != root:
            raise ValueError(f"文件不在允许目录内: {settings.workbuddy_inbox_dir}")
    return path.read_bytes()


def _map_data(parsed: ParsedInvoice, source_file: str) -> ExtractInvoiceData:
    return ExtractInvoiceData(
        invoiceNumber=parsed.invoice_number,
        invoiceCode=parsed.invoice_code,
        issueDate=parsed.issue_date.isoformat(),
        buyer=PartyInfo(name=parsed.buyer_name, taxId=parsed.buyer_tax_id),
        seller=PartyInfo(name=parsed.seller_name, taxId=parsed.seller_tax_id),
        amountWithoutTax=str(parsed.amount_without_tax),
        taxAmount=str(parsed.tax_amount),
        totalWithTax=str(parsed.total_amount),
        totalWithTaxCN=parsed.total_amount_cn,
        invoiceType=parsed.invoice_type,
        sourceFile=source_file,
        extractedAt=datetime.now(timezone.utc).isoformat(),
    )


def extract_invoice_file(file_path: str) -> ExtractResult:
    data = _read_file(file_path)
    kind = classify_attachment(Path(file_path).name, "", data)
    if kind == "IMAGE":
        return ExtractResult(success=False, error=IMAGE_REJECT)
    if kind not in _SUPPORTED:
        return ExtractResult(success=False, error=f"不支持的格式: {kind or '未知'}")
    try:
        outcome = parse_file(kind, data)
        if outcome.parsed is None:
            if outcome.source == "PDF_UNSTRUCTURED":
                return ExtractResult(success=False, error=UNSTRUCTURED)
            detail = outcome.errors[0].message if outcome.errors else "解析失败"
            return ExtractResult(success=False, error=f"解析失败: {detail}")
        validation_errors = [{"code": e.code, "message": e.message} for e in outcome.errors]
        return ExtractResult(
            success=True,
            data=_map_data(outcome.parsed, file_path),
            validation=ValidationResult(valid=not validation_errors, errors=validation_errors),
        )
    except Exception as e:
        return ExtractResult(success=False, error=f"解析失败: {e}")


def batch_extract_invoice_files(file_paths: list[str]) -> list[ExtractResult]:
    results: list[ExtractResult] = []
    for p in file_paths:
        try:
            results.append(extract_invoice_file(p))
        except Exception as e:  # 单条任何异常都不影响其他条目（契约：逐条 success/error）
            results.append(ExtractResult(success=False, error=str(e)))
    return results


def validate_invoice_data(invoice_data: dict) -> ValidationResult:
    if not isinstance(invoice_data, dict):
        return ValidationResult(valid=False, errors=[{"code": "INVALID_FIELD", "message": "invoice_data 必须是对象"}])
    required = ["invoiceNumber", "issueDate", "amountWithoutTax", "taxAmount", "totalWithTax"]
    missing = [
        {"code": "MISSING_FIELD", "message": f"缺少必填字段: {f}"}
        for f in required
        if invoice_data.get(f) in (None, "")
    ]
    if missing:
        return ValidationResult(valid=False, errors=missing)
    try:
        parsed = ParsedInvoice(
            invoice_number=str(invoice_data["invoiceNumber"]),
            issue_date=date.fromisoformat(str(invoice_data["issueDate"])),
            amount_without_tax=Decimal(str(invoice_data["amountWithoutTax"])),
            tax_amount=Decimal(str(invoice_data["taxAmount"])),
            total_amount=Decimal(str(invoice_data["totalWithTax"])),
            total_amount_cn=str(invoice_data.get("totalWithTaxCN") or ""),
            seller_name=str(_party(invoice_data.get("seller")).get("name") or ""),
            seller_tax_id=str(_party(invoice_data.get("seller")).get("taxId") or ""),
            buyer_name=str(_party(invoice_data.get("buyer")).get("name") or ""),
            buyer_tax_id=str(_party(invoice_data.get("buyer")).get("taxId") or ""),
            confidence_score=1.0,
            parse_source="workbuddy",
        )
        core_errors = [{"code": e.code, "message": e.message} for e in core_validate(parsed)]
    except (ValueError, InvalidOperation) as e:
        return ValidationResult(valid=False, errors=[{"code": "INVALID_FIELD", "message": str(e)}])
    return ValidationResult(valid=not core_errors, errors=core_errors)
