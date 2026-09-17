from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import Money, utcnow


class BankReceipt(Base):
    """银行回单（数字员工 P3/R1）：支出凭证，与发票配对建议（不接银行 API）。"""

    __tablename__ = "bank_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    # 所属上传批次文件 SHA256（溯源/清理；防重在 receipt_uploads 层）
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    counterparty_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    abstract: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 收付方向（P2）：收/付/内部（银行内部交易如手续费/利息）；凭证借贷方向依此
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 银行识别（多银行 P0）：ccb/icbc/abc/cmb/boc；未识别为 None
    bank_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 原件定位（R1.2）：页码 + 锚点（归一化 bbox/锚点串/算法版本）
    page_no: Mapped[int | None] = mapped_column(nullable=True)
    anchor: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 质量校验（P0）：命中问题项需人工核对（空户名/账号残留/本司账户行/金额缺失等）
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_issues: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 交易性质（2026-09-17 分类体系）：tax/social/bank_fee/salary/internal_transfer/
    # sales_collection/treasury_in/purchase/unknown——决定「是否需要发票」与凭证类型建议
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    # 分类来源：rule（规则判定/重分类）或 manual（人工覆盖；重分类默认跳过）
    category_source: Mapped[str] = mapped_column(String(8), nullable=False, default="rule")

    paired_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/paired/unmatched

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow,
        onupdate=func.now(), nullable=False,
    )


class ReceiptUpload(Base):
    """回单上传批次（R1.1 异步解析）：一次上传一份文件，解析由后台任务执行。

    file_hash 唯一 → 同一文件重复上传被拒（409）；status:
    parsing（解析中）/ parsed（完成，receipt_count 为入库张数）/ failed（error 为原因）。
    """

    __tablename__ = "receipt_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="parsing")
    receipt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
