"""Agent 运维：自检项存在 + 会话保留清理。"""
from datetime import datetime, timedelta, timezone

from invoicing.models import AgentMessage, AgentSession, User
from invoicing.ops.checks import run_all_checks


def test_check_agent_llm_present():
    names = [c["name"] for c in run_all_checks()]
    assert "agent_llm" in names


def test_purge_expired_sessions(db):
    from invoicing.scheduler import _purge_expired_agent_sessions

    user = User(username="purge_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    old = AgentSession(user_id=user.id, updated_at=datetime.now(timezone.utc) - timedelta(days=200))
    fresh = AgentSession(user_id=user.id)
    db.add_all([old, fresh])
    db.commit()
    db.add(AgentMessage(session_id=old.id, role="user", content="旧"))
    db.commit()

    removed = _purge_expired_agent_sessions(db, retention_days=90)
    assert removed == 1
    assert db.get(AgentSession, old.id) is None
    assert db.query(AgentMessage).filter_by(session_id=old.id).count() == 0  # CASCADE
    assert db.get(AgentSession, fresh.id) is not None
