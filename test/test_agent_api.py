# test/test_agent_api.py
"""Agent API：会话 CRUD 鉴权 + SSE 流式（假 LLM）+ 审计落库。"""
import time

import pytest
from fastapi.testclient import TestClient

from invoicing.main import create_app
from invoicing.models import AuditLog


@pytest.fixture()
def client(db):
    with TestClient(create_app()) as c:
        yield c


def _login(client, username="admin", password="admin123") -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_sessions_crud_and_ownership(client, db):
    h = _login(client)
    r = client.post("/api/v1/agent/sessions", headers=h)
    assert r.status_code == 200
    sid = r.json()["id"]
    assert client.get("/api/v1/agent/sessions", headers=h).json()[0]["id"] == sid

    # 另一个用户看不到/删不掉别人的会话（会话归属隔离）
    from invoicing.models import User
    from invoicing.security import hash_password
    other = User(username="other_u", password_hash=hash_password("pass1234"), role="employee")
    db.add(other)
    db.commit()
    h2 = _login(client, "other_u", "pass1234")
    assert client.get(f"/api/v1/agent/sessions/{sid}/messages", headers=h2).status_code == 404
    assert client.delete(f"/api/v1/agent/sessions/{sid}", headers=h2).status_code == 404


def test_chat_streams_with_fake_llm(client, db, monkeypatch):
    h = _login(client)
    sid = client.post("/api/v1/agent/sessions", headers=h).json()["id"]

    # 假 AsyncOpenAI：create(stream=True) 返回只吐一段 final 文本的异步迭代器
    class _Delta:
        def __init__(self, c): self.delta = type("M", (), {"content": c})()

    class _Chunk:
        def __init__(self, c): self.choices = [_Delta(c)]; self.usage = None

    class _Completions:
        async def create(self, **kw):
            assert kw.get("stream") is True
            async def _gen():
                yield _Chunk("你好")
                yield _Chunk("，我是助手")
            return _gen()

    fake = type("F", (), {"chat": type("C", (), {"completions": _Completions()})()})()

    import invoicing.api.agent as agent_api
    monkeypatch.setattr(agent_api, "get_agent_client", lambda: fake)

    with client.stream("POST", f"/api/v1/agent/sessions/{sid}/messages",
                       json={"message": "你好", "context": {"page": "/"}}, headers=h) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert '"type": "token"' in body and '"type": "done"' in body
    assert "我是助手" in body

    # 等后台落库任务完成（轮询审计行出现，上限 3s）
    deadline = time.time() + 3
    log = None
    while time.time() < deadline:
        db.expire_all()
        log = db.query(AuditLog).filter_by(action="AGENT_CHAT").first()
        if log:
            break
        time.sleep(0.1)
    assert log is not None and log.channel == "web"


def test_chat_llm_disabled_falls_back_static(client, db, monkeypatch):
    """llm_enabled=false（conftest 默认）→ 静态说明，不报错。"""
    h = _login(client)
    sid = client.post("/api/v1/agent/sessions", headers=h).json()["id"]
    import invoicing.api.agent as agent_api
    monkeypatch.setattr(agent_api, "get_agent_client", lambda: None)
    with client.stream("POST", f"/api/v1/agent/sessions/{sid}/messages",
                       json={"message": "你能做什么", "context": {"page": "/"}}, headers=h) as r:
        body = "".join(r.iter_text())
    assert '"type": "token"' in body and "发票查询" in body
