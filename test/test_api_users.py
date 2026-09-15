import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Role, User
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
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return resp.json()["access_token"]


def _seed(db, username, role, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_user_admin_only(client, db):
    admin = _seed(db, "root", Role.admin.value)
    token = _login(client, "root")
    resp = client.post(
        "/api/v1/users",
        json={"username": "caiwu1", "password": "pass12345", "role": "finance_staff"},
        headers=_headers(token),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "finance_staff"


def test_create_user_forbidden_for_finance(client, db):
    staff = _seed(db, "caiwu0", Role.finance_staff.value)
    token = _login(client, "caiwu0")
    resp = client.post(
        "/api/v1/users",
        json={"username": "x", "password": "pass12345", "role": "finance_staff"},
        headers=_headers(token),
    )
    assert resp.status_code == 403


def test_list_users(client, db):
    _seed(db, "root", Role.admin.value)
    token = _login(client, "root")
    resp = client.get("/api/v1/users", headers=_headers(token))
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_create_user_invalid_role_422(client, db):
    _seed(db, "root", Role.admin.value)
    token = _login(client, "root")
    resp = client.post(
        "/api/v1/users",
        json={"username": "bad1", "password": "pass12345", "role": "Finance"},
        headers=_headers(token),
    )
    assert resp.status_code == 422


def test_update_user_invalid_role_422(client, db):
    _seed(db, "root", Role.admin.value)
    token = _login(client, "root")
    resp = client.post(
        "/api/v1/users",
        json={"username": "caiwu1", "password": "pass12345", "role": "finance_staff"},
        headers=_headers(token),
    )
    user_id = resp.json()["id"]
    resp2 = client.put(
        f"/api/v1/users/{user_id}",
        json={"role": "Boss"},
        headers=_headers(token),
    )
    assert resp2.status_code == 422
