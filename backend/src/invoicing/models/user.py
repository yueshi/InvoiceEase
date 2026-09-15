from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.enums import Role
from invoicing.models.fields import utcnow


class UserStatus(str, Enum):
    """账号状态。暂停后**三个闸门**同时失效：登录、已签发 JWT、MCP 令牌（见 security.py）。"""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.employee.value)
    # 暂停 = 立即失效（含已签发的 JWT 与 MCP 令牌），不是"等下次登录才生效"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserStatus.ACTIVE.value,
        server_default=UserStatus.ACTIVE.value,
    )
    # 管理员重置密码后置位：该用户须先改密（避免管理员长期知道他人密码）
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=utcnow,
        onupdate=func.now(),
        nullable=False,
    )
