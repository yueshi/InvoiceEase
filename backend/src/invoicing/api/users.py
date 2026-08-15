from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from invoicing.db import get_db
from invoicing.models import Role, User
from invoicing.schemas.user import UserCreate, UserOut, UserUpdate
from invoicing.security import hash_password, require_role


def _validate_role(role: str) -> None:
    if role not in Role.__members__:
        raise HTTPException(422, f"非法角色: {role}")


router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return db.query(User).order_by(User.id).all()


@router.post("", response_model=UserOut)
def create_user(body: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    _validate_role(body.role)
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "用户名已存在")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if body.password:
        user.password_hash = hash_password(body.password)
    if body.role:
        _validate_role(body.role)
        user.role = body.role
    db.commit()
    return user
