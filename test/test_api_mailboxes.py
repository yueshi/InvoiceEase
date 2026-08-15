import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Mailbox, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _admin_token(client, db):
    db.add(User(username="root", password_hash=hash_password("pass123"), role=Role.admin.value))
    db.flush()
    resp = client.post("/api/v1/auth/login", json={"username": "root", "password": "pass123"})
    return resp.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_create_mailbox_hides_password(client, db):
    token = _admin_token(client, db)
    resp = client.post(
        "/api/v1/mailboxes",
        json={
            "name": "企业邮箱",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "use_ssl": True,
            "username": "inv@example.com",
            "password": "secret123",
            "keywords": "发票,Invoice",
        },
        headers=_h(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "password" not in body


def test_mailbox_crud_admin_only(client, db):
    token = _admin_token(client, db)
    assert client.get("/api/v1/mailboxes", headers=_h(token)).status_code == 200
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role=Role.finance_staff.value))
    db.flush()
    resp = client.post("/api/v1/auth/login", json={"username": "caiwu", "password": "pass123"})
    staff_token = resp.json()["access_token"]
    assert client.get("/api/v1/mailboxes", headers=_h(staff_token)).status_code == 403
