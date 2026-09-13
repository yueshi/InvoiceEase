import pytest
from fastapi.testclient import TestClient

from invoicing.audit import write_audit
from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import AuditLog, Invoice, User


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def test_write_audit(db):
    user = User(username="u1", password_hash="x", role="admin")
    db.add(user)
    db.flush()
    inv = Invoice(file_url="a.xml", file_type="XML")
    db.add(inv)
    db.flush()

    log = write_audit(
        db,
        action="REVIEW",
        user_id=user.id,
        invoice_id=inv.id,
        detail={"note": "通过"},
        ip_address="127.0.0.1",
        channel="web",
    )
    assert log.id is not None
    assert log.action == "REVIEW"
    assert log.detail == {"note": "通过"}
    assert log.channel == "web"


def test_write_audit_system_channel(db):
    log = write_audit(db, action="FETCH", channel="system", detail={"mailbox_id": 1})
    assert log.user_id is None
    assert log.channel == "system"


def test_login_does_not_write_audit(client, db):
    """登录不再写审计（2026-09-13 调整）：LOGIN 属于高频访问而非可审计的业务操作。"""
    from invoicing.models import AuditLog

    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200
    assert db.query(AuditLog).filter(AuditLog.action == "LOGIN").count() == 0


def test_audit_list_hides_legacy_login_rows_by_default(client, db):
    """审计列表默认隐藏历史 LOGIN 行；显式 action=LOGIN 时仍可查（兼容历史数据）。"""
    from invoicing.models import Role, User
    from invoicing.security import hash_password

    db.add(User(username="admin_audit", password_hash=hash_password("pass123"), role=Role.admin.value))
    write_audit(db, action="LOGIN", user_id=1, channel="web")
    write_audit(db, action="PARSE", channel="system", detail={"result": "ok"})
    db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin_audit", "password": "pass123"}
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    default = client.get("/api/v1/audit-logs", headers=auth).json()
    assert all(item["action"] != "LOGIN" for item in default["items"])
    assert any(item["action"] == "PARSE" for item in default["items"])

    explicit = client.get("/api/v1/audit-logs?action=LOGIN", headers=auth).json()
    assert explicit["total"] == 1  # 显式查询仍可回溯历史登录
