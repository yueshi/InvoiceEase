"""MCP 令牌管理 API：自助签发 / 越权拒绝 / 列表范围 / 撤销 / 明文只出现一次。"""
import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import McpToken, Role, User
from invoicing.mcp.identity import ROLE_DEFAULT_SCOPES, SCOPES
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _login(client, username, password="pass123"):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]


def _seed(db, username, role):
    """本文件要走真实登录，口令哈希必须是真的（conftest 已把 bcrypt cost 降到 4）。"""
    user = User(username=username, password_hash=hash_password("pass123"), role=role)
    db.add(user)
    db.flush()
    return user


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_issue_and_list_own_token(client, db):
    _seed(db, "emp", Role.employee.value)
    h = _headers(_login(client, "emp"))

    resp = client.post("/api/v1/mcp-tokens", json={"name": "我的 WorkBuddy"}, headers=h)
    assert resp.status_code == 201
    body = resp.json()
    assert body["plaintext"] and len(body["plaintext"]) >= 40
    assert set(body["token"]["scopes"]) == set(ROLE_DEFAULT_SCOPES["employee"])
    assert body["token"]["state"] == "active"
    assert body["token"]["owner_username"] is None  # 看自己的列表不冗余带户名

    listed = client.get("/api/v1/mcp-tokens", headers=h).json()
    assert len(listed) == 1
    assert "plaintext" not in listed[0]  # 明文只在签发响应里出现一次


def test_issue_for_other_rejected_for_employee(client, db):
    _seed(db, "emp", Role.employee.value)
    other = _seed(db, "other", Role.employee.value)
    h = _headers(_login(client, "emp"))

    resp = client.post(
        "/api/v1/mcp-tokens", json={"name": "t", "user_id": other.id}, headers=h
    )
    assert resp.status_code == 422
    assert "管理员" in resp.json()["detail"]


def test_scope_escalation_rejected_over_api(client, db):
    _seed(db, "emp", Role.employee.value)
    h = _headers(_login(client, "emp"))

    resp = client.post(
        "/api/v1/mcp-tokens", json={"name": "t", "scopes": ["invoice:admin"]}, headers=h
    )
    assert resp.status_code == 422
    assert "超出你权限范围" in resp.json()["detail"]
    assert db.query(McpToken).count() == 0


def test_admin_issues_for_other_and_lists_all(client, db):
    _seed(db, "adm", Role.admin.value)
    emp = _seed(db, "emp", Role.employee.value)
    h = _headers(_login(client, "adm"))

    resp = client.post(
        "/api/v1/mcp-tokens",
        json={"name": "小王的 WorkBuddy", "user_id": emp.id, "scopes": list(SCOPES)},
        headers=h,
    )
    assert resp.status_code == 201
    assert set(resp.json()["token"]["scopes"]) == set(SCOPES)

    # 管理员默认只看自己（空），加 all_users 才看全部
    assert client.get("/api/v1/mcp-tokens", headers=h).json() == []
    allrows = client.get("/api/v1/mcp-tokens?all_users=true", headers=h).json()
    assert [r["owner_username"] for r in allrows] == ["emp"]


def test_token_list_scoped_to_self(client, db):
    _seed(db, "adm", Role.admin.value)
    _seed(db, "emp", Role.employee.value)
    other = _seed(db, "other", Role.employee.value)

    client.post("/api/v1/mcp-tokens", json={"name": "mine"},
                headers=_headers(_login(client, "emp")))
    client.post("/api/v1/mcp-tokens", json={"name": "theirs"},
                headers=_headers(_login(client, "other")))

    mine = client.get("/api/v1/mcp-tokens", headers=_headers(_login(client, "emp"))).json()
    assert [r["name"] for r in mine] == ["mine"]


def test_revoke_takes_effect_immediately(client, db):
    """端到端：签发的明文可用 → 撤销后**同一个明文立即失效**（无需重启）。"""
    import asyncio

    from invoicing.mcp.verifier import MCPTokenVerifier

    _seed(db, "emp", Role.employee.value)
    h = _headers(_login(client, "emp"))

    issued = client.post("/api/v1/mcp-tokens", json={"name": "t"}, headers=h).json()
    plaintext, tid = issued["plaintext"], issued["token"]["id"]
    assert asyncio.run(MCPTokenVerifier().verify_token(plaintext)) is not None

    assert client.post(f"/api/v1/mcp-tokens/{tid}/revoke", headers=h).status_code == 200
    assert asyncio.run(MCPTokenVerifier().verify_token(plaintext)) is None


def test_revoke_others_denied(client, db):
    _seed(db, "emp", Role.employee.value)
    _seed(db, "other", Role.employee.value)

    tid = client.post("/api/v1/mcp-tokens", json={"name": "t"},
                      headers=_headers(_login(client, "other"))).json()["token"]["id"]

    denied = client.post(f"/api/v1/mcp-tokens/{tid}/revoke",
                         headers=_headers(_login(client, "emp")))
    assert denied.status_code == 403 and "无权撤销" in denied.json()["detail"]
    assert db.get(McpToken, tid).revoked_at is None  # 未被撤销


def test_revoke_unknown_token_404(client, db):
    _seed(db, "emp", Role.employee.value)
    resp = client.post("/api/v1/mcp-tokens/999/revoke", headers=_headers(_login(client, "emp")))
    assert resp.status_code == 404


def test_scopes_endpoint_exposes_catalog(client, db):
    _seed(db, "emp", Role.employee.value)
    body = client.get("/api/v1/mcp-tokens/scopes", headers=_headers(_login(client, "emp"))).json()

    assert set(body["scopes"]) == set(SCOPES)
    assert set(body["role_defaults"]) == {"employee", "finance_staff", "finance_manager", "admin"}


def test_requires_authentication(client, db):
    assert client.get("/api/v1/mcp-tokens").status_code in (401, 403)
