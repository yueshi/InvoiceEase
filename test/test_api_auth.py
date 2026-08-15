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


def _seed_user(db, username="zhangsan", role=Role.finance_staff.value, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def test_login_success(client, db):
    _seed_user(db)
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["username"] == "zhangsan"


def test_login_wrong_password(client, db):
    _seed_user(db)
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "wrong"})
    assert resp.status_code == 401


def test_me(client, db):
    user = _seed_user(db)
    resp = client.post("/api/v1/auth/login", json={"username": "zhangsan", "password": "pass123"})
    token = resp.json()["access_token"]
    resp2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200
    assert resp2.json()["id"] == user.id
