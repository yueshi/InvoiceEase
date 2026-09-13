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
    # 登录不写审计（2026-09-13 调整）：高频访问事件会稀释审计日志，
    # 业务可审计性由 FETCH/PARSE/VERIFY/REVIEW/INVOICE_* 等操作保证。
    # 历史 LOGIN 行保留在库中（审计列表默认隐藏，可显式 action=LOGIN 回溯）。
    return TokenResponse(access_token=create_access_token(user), user=user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    write_audit(db, action="LOGOUT", user_id=user.id, channel="web")
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
