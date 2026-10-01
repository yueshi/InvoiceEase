"""Agent 会话/消息模型：ORM 可用 + CASCADE 级联删除。"""
from invoicing.models import AgentMessage, AgentSession, User


def test_session_and_message_roundtrip(db):
    user = User(username="agent_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id, title="测试会话")
    db.add(s)
    db.commit()
    db.add(AgentMessage(session_id=s.id, role="user", content="你好"))
    db.add(AgentMessage(session_id=s.id, role="assistant", content="你好，我是发票易助手",
                        tool_calls=[{"tool": "invoice_list", "status": "done", "ms": 12}]))
    db.commit()

    assert db.query(AgentMessage).filter_by(session_id=s.id).count() == 2
    assert db.query(AgentSession).filter_by(id=s.id).one().title == "测试会话"


def test_cascade_delete(db):
    user = User(username="agent_u2", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()
    db.add(AgentMessage(session_id=s.id, role="user", content="x"))
    db.commit()
    db.delete(s)
    db.commit()
    assert db.query(AgentMessage).filter_by(session_id=s.id).count() == 0
