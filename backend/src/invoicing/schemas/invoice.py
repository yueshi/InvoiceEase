# schemas/invoice.py
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer, computed_field


# 金额类型：内部 Decimal 保持精度，JSON 序列化时强制 2 位小数（金融标准/财会显示习惯）。
# 例如 Decimal('364.7') → API 返回 "364.70"；Decimal('0.06') → "0.06"。
MoneyStr = Annotated[
    Decimal | None,
    PlainSerializer(
        lambda v: None if v is None else f"{v:.2f}",
        return_type=str | None,
        when_used="json",
    ),
]


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    user_id: int | None
    mailbox_id: int | None
    email_subject: str | None
    invoice_code: str | None
    invoice_number: str | None
    issue_date: date | None
    amount_without_tax: MoneyStr
    tax_amount: MoneyStr
    total_amount: MoneyStr
    total_amount_cn: str | None
    seller_name: str | None
    seller_tax_id: str | None
    buyer_name: str | None
    buyer_tax_id: str | None
    invoice_type: str | None
    file_type: str
    parse_source: str | None
    confidence_score: float | None
    validation_errors: list[dict] | None  # 写入方恒为 list[dict]（parse 校验错误/任务兜底错误）
    verify_status: str
    verify_detail: dict | None
    verified_at: datetime | None
    duplicate_flag: bool
    duplicate_of_id: int | None
    status: str
    expense_type: str | None
    cost_center: str | None
    description: str | None
    ai_review_verdict: str | None
    ai_review_reason: str | None
    ai_review_confidence: float | None
    ai_reviewed_at: datetime | None
    red_flag: bool
    review_note: str | None
    reviewed_by: int | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    xml_url: str | None = None  # 合规硬约束：含数字签名的 XML 原件存档地址（财会〔2025〕9 号）

    @computed_field
    @property
    def verify_is_mock(self) -> bool:
        """验真结果为模拟 provider 产出（国税资质未获批前）——展示/汇报必须注明模拟状态。"""
        detail = self.verify_detail or {}
        return str(detail.get("reason", "")).startswith("mock_")


class InvoiceListResponse(BaseModel):
    items: list[InvoiceOut]
    total: int
    page: int
    page_size: int


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None


class InvoiceUpdate(BaseModel):
    """发票业务字段更新（人工复核纠正用）；状态变更走 review/verify 专用端点。

    全字段可选；显式 null 表示清空（str 字段清为 ""，其余字段跳过）。
    """

    invoice_code: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    amount_without_tax: MoneyStr = None
    tax_amount: MoneyStr = None
    total_amount: MoneyStr = None
    total_amount_cn: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    invoice_type: str | None = None
    expense_type: str | None = None
    cost_center: str | None = None
    description: str | None = None
    review_note: str | None = None
