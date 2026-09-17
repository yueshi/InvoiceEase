"""运维 REST：admin 门禁 + 端点行为。"""
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


def _login(client, db, username, role):
    db.add(User(username=username, password_hash=hash_password("pass123"), role=role))
    db.flush()
    return client.post("/api/v1/auth/login",
                       json={"username": username, "password": "pass123"}).json()["access_token"]


def test_ops_requires_admin(client, db):
    token = _login(client, db, "caiwu", Role.finance_staff.value)
    resp = client.get("/api/v1/ops/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_ops_status_ok(client, db):
    token = _login(client, db, "admin2", Role.admin.value)
    resp = client.get("/api/v1/ops/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert {c["name"] for c in body["checks"]} >= {"fernet_key", "default_secrets", "disk_free"}
    assert "last_24h" in body["metrics"]


def test_task_runs_list_and_manual_whitelist(client, db):
    token = _login(client, db, "admin2", Role.admin.value)
    h = {"Authorization": f"Bearer {token}"}
    from invoicing.models.ops import TaskRun

    db.add(TaskRun(task_name="parse", trigger="enqueue", outcome="success", duration_ms=5))
    db.commit()
    resp = client.get("/api/v1/ops/tasks", headers=h)
    assert resp.status_code == 200 and resp.json()["total"] == 1
    resp = client.post("/api/v1/ops/tasks/__nope__/run", headers=h)
    assert resp.status_code == 400


def test_alerts_list(client, db):
    from invoicing.models.ops import OpsAlert

    db.add(OpsAlert(rule_key="disk.low", severity="warning", message="磁盘 8%"))
    db.commit()
    token = _login(client, db, "admin2", Role.admin.value)
    resp = client.get("/api/v1/ops/alerts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json()["total"] == 1


def test_backups_list_empty_ok(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr("invoicing.config.settings.ops_backup_dir", str(tmp_path / "nobackups"))
    token = _login(client, db, "admin2", Role.admin.value)
    resp = client.get("/api/v1/ops/backups", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json() == []
