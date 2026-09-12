"""常用税号及公司信息 CRUD（写 admin 专属；读放开全员——M4 抬头卡片）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import CompanyInfo, CompanyKind, User
from invoicing.schemas.company_info import CompanyInfoCreate, CompanyInfoOut, CompanyInfoUpdate
from invoicing.security import require_role

router = APIRouter(prefix="/company-infos", tags=["company-infos"])

_KINDS = {k.value for k in CompanyKind}


def _validate_kind(kind: str) -> None:
    if kind not in _KINDS:
        raise HTTPException(422, f"非法类型: {kind}（可选 self/supplier/other）")


def _clear_defaults(db: Session) -> None:
    db.query(CompanyInfo).filter(CompanyInfo.is_default.is_(True)).update({"is_default": False})


@router.get("", response_model=list[CompanyInfoOut])
def list_company_infos(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("employee", "finance_staff", "finance_manager", "admin")),
):
    # M4：读放开给员工（开票抬头本就该全员可见）；写操作仍 admin-only
    return db.query(CompanyInfo).order_by(CompanyInfo.id).all()


@router.post("", response_model=CompanyInfoOut)
def create_company_info(body: CompanyInfoCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    _validate_kind(body.kind)
    if db.query(CompanyInfo).filter(CompanyInfo.tax_id == body.tax_id).first():
        raise HTTPException(409, "该税号已存在")
    if body.is_default:
        if body.kind != CompanyKind.self.value:
            raise HTTPException(422, "is_default 仅适用于 kind=self")
        _clear_defaults(db)
    info = CompanyInfo(
        name=body.name,
        tax_id=body.tax_id,
        kind=body.kind,
        is_default=body.is_default,
        remark=body.remark,
        bank_account=body.bank_account,
    )
    db.add(info)
    db.commit()
    return info


@router.put("/{info_id}", response_model=CompanyInfoOut)
def update_company_info(info_id: int, body: CompanyInfoUpdate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    info = db.get(CompanyInfo, info_id)
    if info is None:
        raise HTTPException(404, "记录不存在")
    data = body.model_dump(exclude_unset=True)
    if "kind" in data:
        _validate_kind(data["kind"])
    if "tax_id" in data:
        dup = db.query(CompanyInfo).filter(
            CompanyInfo.tax_id == data["tax_id"], CompanyInfo.id != info_id
        ).first()
        if dup:
            raise HTTPException(409, "该税号已存在")
    effective_kind = data.get("kind") or info.kind
    if data.get("is_default") and effective_kind != CompanyKind.self.value:
        raise HTTPException(422, "is_default 仅适用于 kind=self")
    if data.get("is_default"):
        _clear_defaults(db)
    elif effective_kind != CompanyKind.self.value and info.is_default:
        # kind 改为非 self 时清掉既有默认，避免脏数据（kind=supplier 且 is_default=True）
        data["is_default"] = False
    for field, value in data.items():
        setattr(info, field, value)
    db.commit()
    return info


@router.delete("/{info_id}")
def delete_company_info(info_id: int, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    info = db.get(CompanyInfo, info_id)
    if info is None:
        raise HTTPException(404, "记录不存在")
    db.delete(info)
    db.commit()
    return {"ok": True}
