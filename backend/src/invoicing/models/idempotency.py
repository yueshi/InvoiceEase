"""幂等键表：24 小时窗口，同 (key, tool_name) 重放返回缓存结果（v1.1 §7.5）。

复合主键 (key, tool_name) —— 调用方给同一 key 但调不同工具时不碰撞。
"""
from sqlalchemy import JSON, Column, DateTime, String, func

from invoicing.db import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key = Column(String(64), primary_key=True)
    tool_name = Column(String(64), primary_key=True)
    response = Column(JSON, nullable=False)  # 首次执行的返回，重放时原样返回
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)