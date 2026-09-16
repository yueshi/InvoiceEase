from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from invoicing.config import settings
from invoicing.db import get_db
from invoicing.models import User, UserStatus

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    # cost 可配：生产默认 12；测试设 INVOICING_PASSWORD_HASH_ROUNDS=4（一次哈希省约 4.4 秒）
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=settings.password_hash_rounds)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# 强制改密时仍可访问的 endpoint 在自己身上打标（`func.allow_during_must_change = True`），
# 路径改名也不会失效；缺省默认拦（默认拦是刻意的——将来新增接口不会漏网）。
_ALLOW_DURING_MUST_CHANGE_ATTR = "allow_during_must_change"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未提供认证凭证")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "认证凭证无效或已过期")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    # 闸门 2/3：暂停立即生效——不等 JWT 过期（否则"暂停"最长 8 小时才起作用）
    if user.status == UserStatus.SUSPENDED.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号已暂停，请联系管理员")
    # 闸门 3：强制改密时默认拦、endpoint 自己声明放行
    if user.must_change_password:
        endpoint = getattr(request.scope.get("route"), "endpoint", None)
        if not getattr(endpoint, _ALLOW_DURING_MUST_CHANGE_ATTR, False):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "请先修改密码（管理员已重置你的密码）"
            )
    return user


def require_role(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限执行此操作")
        return user

    return checker
