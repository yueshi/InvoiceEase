"""MCP 访问令牌（per-user）：把 Agent 通道的身份从「一个静态管理员令牌」
改为「一人一令牌」，使角色校验与数据范围隔离生效，审计可追溯到人。

设计见 design/2026-09-13-MCP身份与权限设计.md：
- **只存哈希**：明文仅在签发时返回一次，库内只有 sha256
- **前缀保留**：`token_prefix` 供 UI 展示与审计辨识（不可用于认证）
- **不删只撤**：`revoked_at` 保留"这个令牌做过什么"的追溯起点
- **限期为默认**：`expires_at` 签发默认 90 天（NULL 仅留给服务身份）
- **issuer 进 claims**：鉴权时由 verifier 组装，为阶段 2 多 IdP 的
  `(client_id, iss, subject)` 复合键预留（无需改表）
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class McpToken(Base):
    __tablename__ = "mcp_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # sha256(明文)；唯一索引即认证查询路径
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # 前 8 位，仅供辨识
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # 如「张三的 WorkBuddy」
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )  # 归属人 → 决定 role（数据范围）与默认 scopes
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    scopes: Mapped[str] = mapped_column(String(512), nullable=False)  # 逗号分隔
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 识别僵尸令牌（支撑「多久没用就撤」）
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )  # 自助签发=本人；管理员代发=管理员

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow,
        onupdate=func.now(), nullable=False,
    )
