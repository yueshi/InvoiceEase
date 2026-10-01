# backend/src/invoicing/api/agent.py
"""Web Agent 助手 API：会话 CRUD + SSE 流式对话。

设计见 design/2026-10-01-web-agent-helper-design.md。
- 鉴权：get_current_user（JWT + SUSPENDED + 强制改密闸门全套生效）
- 落库：流结束后后台任务用**独立 session**（请求 session 在流式响应期间生命周期不可靠）
- 断连：响应生成器被取消时置 cancel_event，loop 在下一轮检查点停止
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from invoicing.agent.history import load_session_history
from invoicing.agent.llm import get_agent_client
from invoicing.agent.main_agent import run_agent
from invoicing.agent.observability import record_turn
from invoicing.agent.sse import AgentSSEEmitter
from invoicing.agent.tools_bridge import build_tools_for_user
from invoicing.config import settings
from invoicing.db import SessionLocal, get_db
from invoicing.models import AgentMessage, AgentSession, User
from invoicing.schemas.agent import ChatRequest, MessageOut, SessionOut
from invoicing.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _gate() -> None:
    if not settings.agent_enabled:
        raise HTTPException(404, "智能助手未启用")


def _own_session(db: Session, user: User, session_id: int, *, include_archived: bool = False) -> AgentSession:
    s = db.get(AgentSession, session_id)
    if s is None or s.user_id != user.id or (not include_archived and s.archived_at is not None):
        raise HTTPException(404, "会话不存在")
    return s


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    return (
        db.query(AgentSession)
        .filter(AgentSession.user_id == user.id, AgentSession.archived_at.is_(None))
        .order_by(AgentSession.updated_at.desc())
        .limit(100)
        .all()
    )


@router.post("/sessions", response_model=SessionOut)
def create_session(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/sessions/{session_id}")
def archive_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    from datetime import datetime, timezone

    s = _own_session(db, user, session_id)
    s.archived_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    _own_session(db, user, session_id)
    return (
        db.query(AgentMessage)
        .filter(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.asc())
        .limit(500)
        .all()
    )


@router.post("/sessions/{session_id}/messages")
async def chat(
    session_id: int,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _gate()
    session = _own_session(db, user, session_id)
    history = load_session_history(
        db, session.id,
        max_turns=settings.agent_max_context_turns,
        max_tokens=settings.agent_max_context_tokens,
    )
    tools = await build_tools_for_user(user)
    client = get_agent_client()

    emitter = AgentSSEEmitter()
    cancel_event = asyncio.Event()
    stats: dict = {
        "tool_calls": [], "input_tokens": 0, "output_tokens": 0,
        "duration_ms": 0, "error_code": None, "assistant_reply": "",
    }

    async def _run_and_record() -> None:
        try:
            stats.update(await run_agent(
                message=body.message, context=body.context, emitter=emitter,
                history=history, tools=tools, session_id=str(session.id),
                client=client, cancel_event=cancel_event,
            ))
        except Exception:
            logger.exception("agent run 失败")
            emitter.emit("error", {"code": "AGENT_INTERNAL", "message": "助手内部错误", "retryable": True})
            emitter.emit("done", {"total_ms": 0, "tool_count": 0})
        finally:
            try:
                with SessionLocal() as db2:  # 独立 session：请求依赖的 session 在流式期间生命周期不可靠
                    record_turn(
                        db2, session_id=session.id, user_id=user.id,
                        user_message=body.message,
                        assistant_reply=stats.get("assistant_reply") or "",
                        tool_calls=stats.get("tool_calls") or [],
                        input_tokens=stats.get("input_tokens") or 0,
                        output_tokens=stats.get("output_tokens") or 0,
                        duration_ms=stats.get("duration_ms") or 0,
                        error_code=stats.get("error_code"),
                    )
            except Exception:
                logger.exception("agent 回合落库失败")
            emitter.close()

    asyncio.create_task(_run_and_record())

    async def _frames():
        try:
            async for frame in emitter.stream():
                yield frame
        finally:
            # 客户端断开（关抽屉/刷新）→ 通知 loop 下一轮检查点停止
            cancel_event.set()
            emitter.close()

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
