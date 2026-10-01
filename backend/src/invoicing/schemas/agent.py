# backend/src/invoicing/schemas/agent.py
"""Web Agent 助手请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentContext(BaseModel):
    """前端页面上下文（帮助 LLM 理解「这个/这条」指什么）。"""

    page: str = Field(default="Global", description="当前路由 path，如 /invoices")
    invoice_id: int | None = None
    claim_id: int | None = None      # 报销单 id
    receipt_id: int | None = None
    month: str | None = None         # YYYY-MM


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: AgentContext = Field(default_factory=AgentContext)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str | None
    tool_calls: list | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
