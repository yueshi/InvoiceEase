"""常用企业银行账号（本司账户）：回单「本司账户行」判定与人工核对的基础数据。

账号是判定本司账户的**唯一可靠依据**——公司名称与回单账户户名可能不一致
（实测：公司字典「澜铮鸿欣」vs 回单户名「西安启智合创科技」），账号则稳定。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    account_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    account_name: Mapped[str | None] = mapped_column(String(256), nullable=True)  # 户名（可能与公司名不同）
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 开户行（网点全称）
    # 银行代码（ccb/icbc/abc/cmb/boc）：与回单银行识别同一套；可由开户行自动识别或手选
    bank_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow,
        onupdate=func.now(), nullable=False,
    )
