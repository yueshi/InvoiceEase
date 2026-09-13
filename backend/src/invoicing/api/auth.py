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
    """身份事件审计（等保 2.0 / FRD「全操作留痕」）：成功写 LOGIN，失败写 LOGIN_FAILED。

    失败行记录**尝试的用户名**（未知用户 user_id 为空）——撞库/爆破检测的唯一信号。
    两类事件在审计页归入「安全审计」视图（业务视图默认不显示，避免稀释日常查阅）；
    登录成功行按 audit_login_retention_days 定期清理，失败行长期保留。
    """
    ip = request.client.host if request.client else None
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        write_audit(
            db, action="LOGIN_FAILED", user_id=user.id if user else None, channel="web",
            ip_address=ip, detail={"username": body.username},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    write_audit(db, action="LOGIN", user_id=user.id, channel="web", ip_address=ip)
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
