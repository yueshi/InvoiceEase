from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Invoice, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _login(client, db, username="caiwu", role=Role.finance_staff.value):
    db.add(User(username=username, password_hash=hash_password("pass123"), role=role))
    db.flush()
    return client.post("/api/v1/auth/login", json={"username": username, "password": "pass123"}).json()[
        "access_token"
    ]


def test_stats_overview(client, db):
    token = _login(client, db)
    db.add(Invoice(file_url="a.xml", file_type="XML", status="pending_review"))
    db.add(Invoice(file_url="b.xml", file_type="XML", status="pending_submit"))
    db.add(Invoice(file_url="c.xml", file_type="XML", status="blocked"))
    db.flush()
    resp = client.get("/api/v1/stats/overview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_review"] == 1
    assert body["pending_submit"] == 1
    assert body["today_new"] == 3
