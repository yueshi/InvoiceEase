"""常用税号及公司信息（OCR 纠错字典 + 购买方归属校验 + 预存管理）。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class CompanyInfo(Base):
    __tablename__ = "company_infos"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    tax_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 本司银行账号（kind=self；回单解析判定「本司账户行」用——名称不可靠，账号可靠）
    bank_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, onupdate=func.now(), nullable=False
    )
