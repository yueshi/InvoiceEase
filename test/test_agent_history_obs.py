"""历史加载（配对/截断）+ 回合落库（消息 + 审计 + 首轮标题）。"""
from invoicing.agent.history import load_session_history
from invoicing.agent.observability import record_turn
from invoicing.models import AgentMessage, AgentSession, AuditLog, User


def _mk_session(db, n_pairs: int):
    user = User(username="hist_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()
    for i in range(n_pairs):
        db.add(AgentMessage(session_id=s.id, role="user", content=f"问题{i}"))
        db.add(AgentMessage(session_id=s.id, role="assistant", content=f"回答{i}"))
    db.commit()
    return user, s


def _txt(m):
    """UserMessage 用 content；AssistantMessage 用 text（T2 消息家族字段命名）。"""
    return m.content if m.role == "user" else m.text


def test_history_pairs_and_orders(db):
    _, s = _mk_session(db, 3)
    msgs = load_session_history(db, s.id, max_turns=10, max_tokens=100000)
    assert [m.role for m in msgs] == ["user", "assistant"] * 3
    assert msgs[0].content == "问题0" and _txt(msgs[-1]) == "回答2"


def test_history_truncates_by_turns(db):
    _, s = _mk_session(db, 5)
    msgs = load_session_history(db, s.id, max_turns=2, max_tokens=100000)
    assert len(msgs) == 4
    assert _txt(msgs[-1]) == "回答4"   # 保留最近两轮


def test_history_skips_empty_assistant(db):
    user, s = _mk_session(db, 1)
    db.add(AgentMessage(session_id=s.id, role="assistant", content=""))  # 中间态空文本
    db.add(AgentMessage(session_id=s.id, role="user", content="问题X"))
    db.add(AgentMessage(session_id=s.id, role="assistant", content="回答X"))
    db.commit()
    msgs = load_session_history(db, s.id, max_turns=10)
    assert [_txt(m) for m in msgs] == ["问题0", "回答0", "问题X", "回答X"]


def test_history_empty_assistant_after_user_not_paired(db):
    """user 后紧跟空文本 assistant（工具中间态）不成轮；同用户后续回答正确配对。"""
    user, s = _mk_session(db, 1)
    db.add(AgentMessage(session_id=s.id, role="user", content="问题Y"))
    db.add(AgentMessage(session_id=s.id, role="assistant", content=""))
    db.add(AgentMessage(session_id=s.id, role="assistant", content="回答Y"))
    db.commit()
    msgs = load_session_history(db, s.id, max_turns=10)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[-1].text == "回答Y"


def test_record_turn_sets_title_and_audit(db):
    user, s = _mk_session(db, 0)
    record_turn(db, session_id=s.id, user_id=user.id, user_message="帮我查本月的发票",
                assistant_reply="本月共 3 张", tool_calls=[{"tool": "invoice_list", "status": "done", "ms": 9}],
                input_tokens=10, output_tokens=20, duration_ms=1500, error_code=None)
    db.expire_all()
    assert db.get(AgentSession, s.id).title == "帮我查本月的发票"
    msgs = db.query(AgentMessage).filter_by(session_id=s.id).all()
    assert len(msgs) == 2 and msgs[1].tool_calls[0]["tool"] == "invoice_list"
    log = db.query(AuditLog).filter_by(action="AGENT_CHAT").one()
    assert log.channel == "web" and log.outcome == "success"
    assert log.detail["session_id"] == s.id
