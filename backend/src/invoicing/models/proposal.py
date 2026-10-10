"""v1.1 §7.5 两段握手中的 proposal 表：token 一次有效，15 分钟 TTL。

Agent 调 *_proposal 工具 → 本表落一条待确认提案（不产生业务副作用）；
人工确认后 confirm_execute 消费 token 并落库。
"""
from sqlalchemy import JSON, Column, DateTime, Integer, String, func

from invoicing.db import Base


class Proposal(Base):
    __tablename__ = "proposals"

    token = Column(String(64), primary_key=True)
    tool_name = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, nullable=False)   # 执行时传给 execute 函数的入参
    preview = Column(JSON, nullable=False)   # 给用户看的预览（不落业务库）
    actor_id = Column(Integer, nullable=False)
    actor_type = Column(String(16), nullable=False, default="user")
    channel = Column(String(16), nullable=False, default="mcp")
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)