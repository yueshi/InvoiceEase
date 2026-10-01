"""会话历史加载：agent_messages 表 → Loop messages。

裁剪策略（移植 AuditMind load_session_history）：
- 先限轮数（一轮 = 1 条 user + 1 条 assistant）
- 再按 token 估算从最老一轮整轮丢，不切开一轮
- assistant 空文本（纯工具调用中间态）不算有效轮
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from invoicing.agent.core.context import AssistantMessage, Message, UserMessage
from invoicing.agent.llm import estimate_tokens
from invoicing.models import AgentMessage


def load_session_history(
    db: Session,
    session_id: int,
    max_turns: int = 6,
    max_tokens: int = 8000,
) -> list[Message]:
    if max_turns <= 0:
        return []

    rows = db.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id,
               AgentMessage.role.in_(("user", "assistant")))
        .order_by(AgentMessage.id.desc())
        .limit(max_turns * 2)
    ).scalars().all()

    # 倒序取 → 正序组"轮"：assistant 必须紧跟其 user 且文本非空
    turns: list[tuple[str, str]] = []
    pending_user: str | None = None
    for row in reversed(rows):
        if row.role == "user":
            pending_user = row.content or ""
        elif pending_user is not None and (row.content or "").strip():
            turns.append((pending_user, row.content))
            pending_user = None

    def _token_sum(ts: list[tuple[str, str]]) -> int:
        return sum(estimate_tokens(u) + estimate_tokens(a) for u, a in ts)

    while turns and _token_sum(turns) > max_tokens:
        turns.pop(0)

    messages: list[Message] = []
    for user_text, assistant_text in turns:
        messages.append(UserMessage(content=user_text))
        messages.append(AssistantMessage(text=assistant_text))
    return messages
