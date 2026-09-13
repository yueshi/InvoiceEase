from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.enums import InvoiceStatus, VerifyStatus
from invoicing.models.fields import Money, utcnow


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        # 注意：func 内引用列名必须用 text()（裸字符串会被当作字面量），
        # 而 Index 列表项中的裸字符串才是列名
        Index(
            "uq_invoices_dedup_key",
            "tenant_id",
            func.coalesce(text("invoice_code"), ""),
            "invoice_number",
            unique=True,
        ),
        # 一信多票：同一邮件可含多张发票，按 (message_id, file_url) 复合去重
        Index(
            "uq_invoices_email_message_id",
            "email_message_id",
            "file_url",
            unique=True,
            postgresql_where=text("email_message_id IS NOT NULL"),
            sqlite_where=text("email_message_id IS NOT NULL"),
        ),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id"), nullable=True)
    email_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email_subject: Mapped[str | None] = mapped_column(String(512), nullable=True)

    invoice_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_without_tax: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    total_amount_cn: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seller_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    seller_tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    buyer_tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    xml_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    parse_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list[dict]：校验错误数组

    verify_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VerifyStatus.pending.value
    )
    verify_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    # 发票方向（销项开票在外部系统执行，此处导入已开票用于与收款回单对账）
    # input 进项（收到供应商的票）/ output 销项（我方开给客户的票）
    invoice_direction: Mapped[str] = mapped_column(
        String(8), nullable=False, default="input", server_default="input"
    )
    # 红字票关联的原蓝票（数电红字票面含原发票号码 → 自动关联；否则人工补关联）
    original_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    # 报销维度（与业务状态机正交）：none 未报销 / pending 报销中 / claimed 已报销
    reimbursement_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none"
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=InvoiceStatus.received.value, index=False
    )
    review_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # 费用归类（数字员工 P1）：差旅/办公/招待/采购/其他 + 部门/项目 + 说明
    expense_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_center: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    submitted_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AI 复核预判（数字员工 P1）：建议结论/理由/置信度；None=未生成
    ai_review_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_review_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ai_review_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 红字发票标记（数字员工 P2/M9 第一步）：识别+标记，不自动对冲
    red_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=func.now(),
        nullable=False,
    )
