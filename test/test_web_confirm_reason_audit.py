"""Web 高风险二次确认的「理由」必须落审计（v1.1 §7.2 ✅4 留痕：谁/何时/为什么/结果）。

final review 发现：前端 ConfirmModal 的占位文案承诺「将记入审计」，但 reason
被调用方丢弃、REST 端点也不接收 —— UI 做出虚假承诺，审计缺少「为什么」。
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import AuditLog, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def users(db):
    emp = User(username="emp1", password_hash=hash_password("pass123"),
               role=Role.employee.value)
    fin = User(username="fin1", password_hash=hash_password("pass123"),
               role=Role.finance_staff.value)
    db.add_all([emp, fin])
    db.commit()
    return {"emp": emp, "fin": fin}


def _login(client, username):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": "pass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_claim(db, user, *, amount="110.00"):
    from invoicing.workflow import expenses as svc

    claim = svc.create_claim(db, user, title="大额差旅")
    entry = svc.create_entry(db, user, claim.id, "other", "事项")
    svc.add_manual_voucher(db, user, claim.id, entry.id, voucher_type="internal",
                           amount=Decimal(amount), expense_type="other")
    return claim


def test_submit_with_note_lands_in_audit(client, db, users):
    claim = _seed_claim(db, users["emp"])
    emp = _login(client, "emp1")

    r = client.post(f"/api/v1/expenses/{claim.id}/submit", headers=emp,
                    json={"note": "董事会口头批准"})
    assert r.status_code == 200, r.text

    log = (db.query(AuditLog).filter(AuditLog.action == "EXPENSE_SUBMIT")
           .order_by(AuditLog.id.desc()).first())
    assert log is not None
    assert log.detail.get("note") == "董事会口头批准"


def test_approve_with_note_lands_in_audit(client, db, users):
    claim = _seed_claim(db, users["emp"])
    emp = _login(client, "emp1")
    client.post(f"/api/v1/expenses/{claim.id}/submit", headers=emp, json={})

    fin = _login(client, "fin1")
    r = client.post(f"/api/v1/expenses/{claim.id}/approve", headers=fin,
                    json={"note": "已核对原件与行程"})
    assert r.status_code == 200, r.text

    log = (db.query(AuditLog).filter(AuditLog.action == "EXPENSE_APPROVE")
           .order_by(AuditLog.id.desc()).first())
    assert log is not None
    assert log.detail.get("note") == "已核对原件与行程"


def test_bank_account_delete_with_note_lands_in_audit(client, db, users):
    from invoicing.models import BankAccount

    admin = User(username="admin1", password_hash=hash_password("pass123"),
                 role=Role.admin.value)
    db.add(admin)
    db.commit()
    acc = BankAccount(account_no="6222021234567890", account_name="本司")
    db.add(acc)
    db.commit()

    h = _login(client, "admin1")
    r = client.request("DELETE", f"/api/v1/bank-accounts/{acc.id}", headers=h,
                       json={"note": "账户已销户"})
    assert r.status_code == 200, r.text

    log = (db.query(AuditLog).filter(AuditLog.action == "CONFIG_CHANGE")
           .order_by(AuditLog.id.desc()).first())
    assert log is not None
    assert log.detail.get("note") == "账户已销户"