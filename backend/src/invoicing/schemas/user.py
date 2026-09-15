from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    # 前端路由守卫依赖 must_change_password 把人送到改密页——**缺字段会让强制改密静默失效**
    status: str
    must_change_password: bool
    created_at: datetime


class UserCreate(BaseModel):
    username: str
    password: str
    role: str


class UserUpdate(BaseModel):
    """`PUT /users/{id}` 现仅用于改角色；密码走 POST /users/{id}/password。"""

    password: str | None = None  # 保留字段以避免旧客户端报 schema 错误，服务端会拒绝
    role: str | None = None
