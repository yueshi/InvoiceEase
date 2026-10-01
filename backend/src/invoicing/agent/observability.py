"""Agent 回合落库：agent_messages（会话回放）+ audit_logs（合规审计）。"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import AgentMessage, AgentSession


def record_turn(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    user_message: str,
    assistant_reply: str,
    tool_calls: list[dict],
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    error_code: str | None,
    blocks: list | None = None,
) -> None:
    session = db.get(AgentSession, session_id)
    if session is None:
        return
    if not session.title:
        session.title = (user_message or "").strip()[:40] or "新会话"
    # 显式碰 updated_at：session 行其余字段没变时 onupdate 不会触发，
    # 而侧栏排序依赖它（SQLAlchemy onupdate 仅在本行有 UPDATE 时生效）
    session.updated_at = datetime.now(timezone.utc)

    db.add(AgentMessage(session_id=session_id, role="user", content=user_message))
    db.add(AgentMessage(
        session_id=session_id, role="assistant", content=assistant_reply or "",
        tool_calls=tool_calls, blocks=blocks, duration_ms=duration_ms,
        input_tokens=input_tokens, output_tokens=output_tokens,
    ))
    write_audit(
        db, action="AGENT_CHAT", user_id=user_id, channel="web",
        detail={
            "session_id": session_id,
            "tool_calls": [t.get("tool") for t in tool_calls],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": duration_ms,
            "error_code": error_code,
        },
    )
    db.commit()
