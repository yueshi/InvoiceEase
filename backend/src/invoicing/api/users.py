"""用户与权限管理 API：管理员建号 / 改角色 / 暂停恢复 / 重置密码。

自助改密在 `POST /auth/password`（登录用户操作自己的账号，见 api/auth.py）。
`PUT /users/{id}` 收窄为**仅改角色**——密码走独立接口（语义不同：重置还要置
「下次登录须改密」，混在一个接口里会含糊）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import User
from invoicing.schemas.user import UserCreate, UserOut, UserUpdate
from invoicing.security import require_role
from invoicing.workflow import users as svc

router = APIRouter(prefix="/users", tags=["users"])


class ResetPasswordIn(BaseModel):
    """重置密码：自动生成 或 手动指定，二选一；都不给则自动生成。"""

    new_password: str | None = Field(default=None, min_length=svc.PASSWORD_MIN_LENGTH)
    generate: bool = False


class ResetPasswordOut(BaseModel):
    user: UserOut
    # 仅自动生成时返回（一次性展示，同 MCP 令牌的明文策略）
    plaintext: str | None = None


def _run(fn, *args, **kwargs):
    """service 层 ValueError → HTTP；保持 API 层薄。"""
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if "不存在" in msg else 422, msg) from None


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut)
def create_user(
    body: UserCreate, db: Session = Depends(get_db), actor: User = Depends(require_role("admin"))
):
    return _run(svc.create_user, db, actor, body.username, body.password, body.role)


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """调整角色（防自锁与审计在 service 层）。"""
    if body.role is None:
        raise HTTPException(422, "改密码请用 POST /users/{id}/password，暂停请用 /suspend")
    return _run(svc.set_role, db, actor, user_id, body.role)


@router.post("/{user_id}/password", response_model=ResetPasswordOut)
def reset_password(
    user_id: int,
    body: ResetPasswordIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role("admin")),
):
    """管理员重置他人密码；该用户下次登录须先改密。"""
    generate = body.generate or not body.new_password
    target, plaintext = _run(
        svc.reset_password, db, actor, user_id,
        new_password=body.new_password, generate=generate,
    )
    return ResetPasswordOut(
        user=UserOut.model_validate(target, from_attributes=True), plaintext=plaintext
    )


@router.post("/{user_id}/suspend", response_model=UserOut)
def suspend_user(
    user_id: int, db: Session = Depends(get_db), actor: User = Depends(require_role("admin"))
):
    """暂停：登录、已签发 JWT、MCP 令牌**同时立即失效**。"""
    return _run(svc.set_status, db, actor, user_id, suspend=True)


@router.post("/{user_id}/resume", response_model=UserOut)
def resume_user(
    user_id: int, db: Session = Depends(get_db), actor: User = Depends(require_role("admin"))
):
    return _run(svc.set_status, db, actor, user_id, suspend=False)
