"""常用税号及公司信息 schema。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

TAX_ID_PATTERN = r"^[0-9A-Z]{18}$"


class CompanyInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tax_id: str
    kind: str
    is_default: bool
    remark: str | None
    bank_account: str | None = None  # 本司银行账号（kind=self；回单本司账户行判定）
    created_at: datetime
    updated_at: datetime


class CompanyInfoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    tax_id: str = Field(pattern=TAX_ID_PATTERN)
    kind: str = "other"
    is_default: bool = False
    remark: str | None = None
    bank_account: str | None = Field(default=None, max_length=64)


class CompanyInfoUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    tax_id: str | None = Field(default=None, pattern=TAX_ID_PATTERN)
    kind: str | None = None
    is_default: bool | None = None
    remark: str | None = None
    bank_account: str | None = Field(default=None, max_length=64)
