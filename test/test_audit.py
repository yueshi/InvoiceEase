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


def test_login_writes_audit_and_failed_login_recorded(client, db):
    """身份事件审计（等保/FRD「全操作留痕」）：登录成功写 LOGIN；
    登录失败写 LOGIN_FAILED（含尝试的用户名，未知用户 user_id 为空）。"""
    from invoicing.models import AuditLog

    ok = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert ok.status_code == 200
    log = db.query(AuditLog).filter(AuditLog.action == "LOGIN").order_by(AuditLog.id.desc()).first()
    assert log is not None and log.user_id is not None

    bad = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401
    failed = (
        db.query(AuditLog).filter(AuditLog.action == "LOGIN_FAILED").order_by(AuditLog.id.desc()).first()
    )
    assert failed is not None
    assert failed.detail.get("username") == "admin"

    unknown = client.post("/api/v1/auth/login", json={"username": "no_such_user", "password": "x"})
    assert unknown.status_code == 401
    last = (
        db.query(AuditLog).filter(AuditLog.action == "LOGIN_FAILED").order_by(AuditLog.id.desc()).first()
    )
    assert last.detail.get("username") == "no_such_user"
    assert last.user_id is None  # 未知用户无 user_id，但尝试名保留（撞库检测）


def test_audit_category_views(client, db):
    """审计视图分类：business（默认，不含身份事件）/ security（仅身份事件）/ all。"""
    from invoicing.models import Role, User
    from invoicing.security import hash_password

    db.add(User(username="admin_audit2", password_hash=hash_password("pass123"), role=Role.admin.value))
    write_audit(db, action="LOGIN", user_id=1, channel="web")
    write_audit(db, action="LOGIN_FAILED", channel="web", detail={"username": "attacker"})
    write_audit(db, action="PARSE", channel="system", detail={"result": "ok"})
    db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"username": "admin_audit2", "password": "pass123"}
    ).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    biz = client.get("/api/v1/audit-logs", headers=auth).json()
    biz_actions = {i["action"] for i in biz["items"]}
    assert "PARSE" in biz_actions
    assert not ({"LOGIN", "LOGIN_FAILED", "LOGOUT"} & biz_actions)

    sec = client.get("/api/v1/audit-logs?category=security", headers=auth).json()
    sec_actions = {i["action"] for i in sec["items"]}
    assert {"LOGIN", "LOGIN_FAILED"} <= sec_actions
    assert "PARSE" not in sec_actions

    allv = client.get("/api/v1/audit-logs?category=all", headers=auth).json()
    all_actions = {i["action"] for i in allv["items"]}
    assert {"PARSE", "LOGIN", "LOGIN_FAILED"} <= all_actions

    # 显式 action 查询优先于分类（历史回溯）
    explicit = client.get("/api/v1/audit-logs?action=LOGIN_FAILED", headers=auth).json()
    assert explicit["total"] >= 1


def test_audit_login_retention_purge(db):
    """保留期：登录成功行按天数清理；失败行长期保留（安全价值最高）。"""
    from datetime import timedelta

    from invoicing.models.fields import utcnow
    from invoicing.scheduler import _purge_expired_login_audits

    old = utcnow() - timedelta(days=200)
    fresh = utcnow() - timedelta(days=1)
    stale = write_audit(db, action="LOGIN", channel="web")
    stale.created_at = old
    keep = write_audit(db, action="LOGIN", channel="web")
    keep.created_at = fresh
    failed_old = write_audit(db, action="LOGIN_FAILED", channel="web", detail={"username": "x"})
    failed_old.created_at = old
    db.commit()

    removed = _purge_expired_login_audits(db, retention_days=90)
    assert removed == 1
    from invoicing.models import AuditLog

    ids = {row.id for row in db.query(AuditLog).all()}
    assert stale.id not in ids
    assert keep.id in ids
    assert failed_old.id in ids  # 失败登录不清理
