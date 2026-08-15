from fastapi import APIRouter, Depends, HTTPException, Request, status

from invoicing.audit import write_audit
from invoicing.db import get_db
from invoicing.models import User
from invoicing.schemas.auth import LoginRequest, TokenResponse
from invoicing.schemas.user import UserOut
from invoicing.security import create_access_token, get_current_user, verify_password
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    write_audit(
        db, action="LOGIN", user_id=user.id, channel="web",
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return TokenResponse(access_token=create_access_token(user), user=user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    write_audit(db, action="LOGOUT", user_id=user.id, channel="web")
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
