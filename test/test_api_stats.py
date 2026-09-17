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


def test_stats_overview_scoped_by_role(client, db):
    """FRD §3.5.2：员工「仅本人相关发票」，财务「全公司」——工作台数字须随角色收敛。

    回归：overview 曾对任何登录用户返回全库计数（所有人看到同一组数字）。
    """
    emp_token = _login(client, db, username="emp1", role=Role.employee.value)
    fin_token = _login(client, db, username="fin1")
    emp = db.query(User).filter(User.username == "emp1").one()
    other = User(username="emp2", password_hash=hash_password("pass123"), role=Role.employee.value)
    db.add(other)
    db.flush()
    db.add_all(
        [
            Invoice(file_url="mine-1.xml", file_type="XML", status="pending_review", user_id=emp.id),
            Invoice(file_url="mine-2.xml", file_type="XML", status="pending_submit", user_id=emp.id),
            Invoice(file_url="others.xml", file_type="XML", status="pending_review", user_id=other.id),
        ]
    )
    db.flush()

    def overview(token: str) -> dict:
        resp = client.get("/api/v1/stats/overview", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        return resp.json()

    emp_body = overview(emp_token)
    assert emp_body["pending_review"] == 1  # 仅本人的待复核
    assert emp_body["pending_submit"] == 1
    assert emp_body["today_new"] == 2  # 他人的票不计入

    fin_body = overview(fin_token)
    assert fin_body["pending_review"] == 2  # 财务看全公司
    assert fin_body["today_new"] == 3


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
