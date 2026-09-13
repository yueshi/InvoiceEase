"""报销单与明细（P0 轻量闭环：防重复报销 + 合规凭证 + 可追溯）。

设计要点（见 design/2026-09-13-发票易报销功能设计.md）：
- **一票一报**：明细行 active=True 时 invoice_id 唯一（部分唯一索引），
  报销单被驳回/撤回时明细置 active=False 释放占用；发票可被历史单据引用但不可重复占用
- 状态机：draft → pending_approval → approved / rejected（单级财务审批）+ withdrawn
- 无票支出：明细可用银行回单（voucher_type=bank_receipt/tax_receipt）或人工凭证类型
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import Money, utcnow


class ExpenseClaimStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class VoucherType(str, Enum):
    """明细凭证类型（无票支出的合规分档，见设计 §3.3b）。"""

    INVOICE = "invoice"  # 发票（正常路径）
    BANK_RECEIPT = "bank_receipt"  # 银行手续费/利息/转账回单
    TAX_RECEIPT = "tax_receipt"  # 社保/公积金/税费缴款书回单
    RECEIPT_VOUCHER = "receipt_voucher"  # 收款凭证（小额零星个人；需姓名+身份证号）
    INTERNAL = "internal"  # 内部凭证（工资/差旅补助）
    CONTRACT = "contract"  # 合同/协议类（违约金等，特殊情形）
    OVERSEAS = "overseas"  # 境外票据


class ExpenseClaim(Base):
    __tablename__ = "expense_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    claim_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ExpenseClaimStatus.DRAFT
    )
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow,
        onupdate=func.now(), nullable=False,
    )


class ExpenseItem(Base):
    __tablename__ = "expense_items"
    __table_args__ = (
        # 一票一报：active 明细的发票唯一（部分唯一索引，SQLite/PG 均支持）
        Index(
            "uq_expense_item_active_invoice",
            "invoice_id",
            unique=True,
            sqlite_where=text("active = 1 AND invoice_id IS NOT NULL"),
            postgresql_where=text("active AND invoice_id IS NOT NULL"),
        ),
        Index(
            "uq_expense_item_active_receipt",
            "receipt_id",
            unique=True,
            sqlite_where=text("active = 1 AND receipt_id IS NOT NULL"),
            postgresql_where=text("active AND receipt_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("expense_claims.id", ondelete="CASCADE"), nullable=False
    )
    invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    receipt_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_receipts.id", ondelete="SET NULL"), nullable=True
    )
    voucher_type: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    expense_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 无票支出凭证要素（28 号公告：收款凭证需姓名+身份证号）
    payee_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payee_id_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deductible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deductible_note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 占用标记：报销单驳回/撤回后置 False，释放发票/回单（一票一报约束即时解除）
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
