"""常用企业银行账号 API：本司账户维护（回单「本司账户行」判定的基础数据）。"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import BankAccount, User
from invoicing.security import require_role

router = APIRouter(prefix="/bank-accounts", tags=["bank-accounts"])

_FINANCE = ("finance_staff", "finance_manager", "admin")
_ACCOUNT_NO_RE = re.compile(r"^[0-9]{6,32}$")


class BankAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_no: str
    account_name: str | None
    bank_name: str | None
    remark: str | None
    is_default: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class BankAccountCreate(BaseModel):
    account_no: str = Field(min_length=6, max_length=64)
    account_name: str | None = Field(default=None, max_length=256)
    bank_name: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=256)
    is_default: bool = False
    enabled: bool = True


class BankAccountUpdate(BaseModel):
    account_no: str | None = Field(default=None, min_length=6, max_length=64)
    account_name: str | None = Field(default=None, max_length=256)
    bank_name: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=256)
    is_default: bool | None = None
    enabled: bool | None = None


def _normalize(account_no: str) -> str:
    """账号规范化：去空格/连字符（回单打印常带分隔）。"""
    return re.sub(r"[\s\-]", "", account_no or "")


def _validate(account_no: str) -> str:
    normalized = _normalize(account_no)
    if not _ACCOUNT_NO_RE.match(normalized):
        raise HTTPException(422, "账号须为 6-32 位数字")
    return normalized


def _clear_defaults(db: Session, keep_id: int | None = None) -> None:
    q = db.query(BankAccount).filter(BankAccount.is_default.is_(True))
    if keep_id is not None:
        q = q.filter(BankAccount.id != keep_id)
    q.update({"is_default": False})


@router.get("", response_model=list[BankAccountOut])
def list_bank_accounts(db: Session = Depends(get_db), _: User = Depends(require_role(*_FINANCE))):
    return db.query(BankAccount).order_by(BankAccount.id).all()


@router.post("", response_model=BankAccountOut)
def create_bank_account(
    body: BankAccountCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))
):
    account_no = _validate(body.account_no)
    if db.query(BankAccount).filter(BankAccount.account_no == account_no).first():
        raise HTTPException(409, "该账号已存在")
    if body.is_default:
        _clear_defaults(db)
    acc = BankAccount(
        account_no=account_no,
        account_name=(body.account_name or "").strip() or None,
        bank_name=(body.bank_name or "").strip() or None,
        remark=body.remark,
        is_default=body.is_default,
        enabled=body.enabled,
    )
    db.add(acc)
    db.commit()
    return acc


@router.put("/{account_id}", response_model=BankAccountOut)
def update_bank_account(
    account_id: int,
    body: BankAccountUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    acc = db.get(BankAccount, account_id)
    if acc is None:
        raise HTTPException(404, "账号不存在")
    data = body.model_dump(exclude_unset=True)
    if "account_no" in data and data["account_no"] is not None:
        account_no = _validate(data["account_no"])
        dup = (
            db.query(BankAccount)
            .filter(BankAccount.account_no == account_no, BankAccount.id != account_id)
            .first()
        )
        if dup:
            raise HTTPException(409, "该账号已存在")
        data["account_no"] = account_no
    if data.get("is_default"):
        _clear_defaults(db, keep_id=account_id)
    for field, value in data.items():
        if field in ("account_name", "bank_name") and isinstance(value, str):
            value = value.strip() or None
        setattr(acc, field, value)
    db.commit()
    return acc


@router.delete("/{account_id}")
def delete_bank_account(
    account_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))
):
    acc = db.get(BankAccount, account_id)
    if acc is None:
        raise HTTPException(404, "账号不存在")
    db.delete(acc)
    db.commit()
    return {"ok": True}
