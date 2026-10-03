import time
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from invoicing.audit import write_audit
from invoicing.config import settings
from invoicing.db import get_db
from invoicing.models import User, UserStatus, WebTicket
from invoicing.models.fields import utcnow
from invoicing.schemas.auth import LoginRequest, TicketLoginRequest, TokenResponse
from invoicing.schemas.user import UserOut
from invoicing.security import create_access_token, get_current_user, verify_password
from invoicing.web_links import decode_ticket
from invoicing.workflow import users as users_svc
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])

# ponytail: 进内滑动窗口，单进程部署有效；multi-worker / redis 部署时换共享存储
_login_failures: dict[tuple[str, str], list[float]] = {}


def _recent_failures(key: tuple[str, str], now: float, window: float) -> list[float]:
    attempts = [t for t in _login_failures.get(key, []) if now - t < window]
    _login_failures[key] = attempts
    return attempts


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """自助改密（须验旧密码）。管理员重置后强制改密也走这里，改完即解除。"""
    try:
        users_svc.change_own_password(db, user, body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"ok": True}


# 强制改密期间必须能访问（否则用户无法完成改密，把自己锁死）。
# 标在 endpoint 函数本身——路径改名不会失效；get_current_user 通过 route.endpoint
# 反查这个标记。
change_password.allow_during_must_change = True


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """身份事件审计（等保 2.0 / FRD「全操作留痕」）：成功写 LOGIN，失败写 LOGIN_FAILED。

    失败行记录**尝试的用户名**（未知用户 user_id 为空）——撞库/爆破检测的唯一信号。
    两类事件在审计页归入「安全审计」视图（业务视图默认不显示，避免稀释日常查阅）；
    登录成功行按 audit_login_retention_days 定期清理，失败行长期保留。
    """
    ip = request.client.host if request.client else None
    user = db.query(User).filter(User.username == body.username).first()
    # 闸门 0：登录限流——同 (ip, username) 滑动窗口内连续失败达上限即锁定（锁定期内正确密码同样拒绝）
    key = (ip or "", body.username)
    now = time.time()
    window = settings.login_lockout_minutes * 60
    attempts = _recent_failures(key, now, window)
    if len(attempts) >= settings.login_max_failures:
        wait_min = int((window - (now - attempts[0])) // 60) + 1
        write_audit(
            db, action="LOGIN_FAILED", user_id=user.id if user else None, channel="web",
            ip_address=ip, detail={"username": body.username, "reason": "rate_limited"},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, f"登录尝试次数过多，请约 {wait_min} 分钟后重试"
        )
    if user is None or not verify_password(body.password, user.password_hash):
        attempts.append(now)
        write_audit(
            db, action="LOGIN_FAILED", user_id=user.id if user else None, channel="web",
            ip_address=ip, detail={"username": body.username},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    # 闸门 1：暂停用户登不进来。审计带 reason，避免与"密码错"混在一起
    # （否则看不出有人在反复尝试登录一个已停用的账号）。
    if user.status == UserStatus.SUSPENDED.value:
        write_audit(
            db, action="LOGIN_FAILED", user_id=user.id, channel="web", ip_address=ip,
            detail={"username": body.username, "reason": "suspended"},
        )
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已暂停，请联系管理员")
    write_audit(db, action="LOGIN", user_id=user.id, channel="web", ip_address=ip)
    _login_failures.pop(key, None)  # 成功登录清零失败计数
    db.commit()
    return TokenResponse(access_token=create_access_token(user), user=user)


@router.post("/ticket-login", response_model=TokenResponse)
def ticket_login(body: TicketLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Agent 深链免登票据兑换（design/2026-10-03-Agent跳转Web后台-深链与免登票据设计.md §5）。

    一次性：兑换即用 jti 抢占写表（主键冲突=已用过）；过期/无效/用途不符统一 401 不泄漏细节。
    成功走与普通登录同一闸门（SUSPENDED 拒），审计 channel="web_link"；**票据原文不入审计**。
    """
    ip = request.client.host if request.client else None

    def _fail(reason: str) -> HTTPException:
        write_audit(db, action="LOGIN_FAILED", user_id=None, channel="web_link",
                    ip_address=ip, detail={"reason": reason})
        db.commit()
        return HTTPException(status.HTTP_401_UNAUTHORIZED, "登录链接已失效，请重新登录")

    try:
        payload = decode_ticket(body.ticket)
        user_id = int(payload["sub"])
        expires_at = datetime.fromtimestamp(
            int(payload["exp"]), tz=timezone.utc
        ).replace(tzinfo=None)  # naive-UTC 入库，与 utcnow() 一致
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise _fail("invalid_ticket") from None
    user = db.get(User, user_id)
    if user is None:
        raise _fail("unknown_user") from None
    if user.status == UserStatus.SUSPENDED.value:
        write_audit(db, action="LOGIN_FAILED", user_id=user.id, channel="web_link",
                    ip_address=ip, detail={"reason": "suspended"})
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已暂停，请联系管理员")
    # 一次性抢占：主键冲突即「已用过」；顺手清理过期行（量级极小，无需 cron）
    db.query(WebTicket).filter(WebTicket.expires_at < utcnow()).delete()
    try:
        db.add(WebTicket(jti=payload["jti"], user_id=user_id, expires_at=expires_at))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _fail("ticket_used") from None
    write_audit(db, action="LOGIN", user_id=user.id, channel="web_link", ip_address=ip)
    db.commit()
    return TokenResponse(access_token=create_access_token(user), user=user)


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    write_audit(db, action="LOGOUT", user_id=user.id, channel="web")
    db.commit()
    return {"ok": True}


# /me 也得放行：路由守卫读 user.must_change_password 就要拉一次 me
logout.allow_during_must_change = True


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


me.allow_during_must_change = True
