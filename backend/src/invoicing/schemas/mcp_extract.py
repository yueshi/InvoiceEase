"""WorkBuddy 识别工具输出模型（字段名对齐集成设计 V0.1 §三，camelCase）。"""
from pydantic import BaseModel


class PartyInfo(BaseModel):
    name: str | None = None
    taxId: str | None = None
    addressPhone: str | None = None
    bankAccount: str | None = None


class ExtractInvoiceData(BaseModel):
    invoiceType: str | None = None
    invoiceNumber: str
    invoiceCode: str | None = None
    issueDate: str
    checkCode: str | None = None
    buyer: PartyInfo
    seller: PartyInfo
    amountWithoutTax: str
    taxAmount: str
    totalWithTax: str
    totalWithTaxCN: str | None = None
    items: list[dict] = []
    sourceFile: str
    extractedAt: str


class ValidationError(BaseModel):
    code: str
    message: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []


class ExtractResult(BaseModel):
    success: bool
    error: str | None = None
    data: ExtractInvoiceData | None = None
    validation: ValidationResult | None = None
