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


def test_backups_download_rejects_path_traversal(client, db, tmp_path, monkeypatch):
    monkeypatch.setattr("invoicing.config.settings.ops_backup_dir", str(tmp_path / "bk"))
    token = _login(client, db, "admin2", Role.admin.value)
    h = {"Authorization": f"Bearer {token}"}
    for name in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd",
                 "invoiceease-backup-20260917-120000.tar.gz"):
        resp = client.get(f"/api/v1/ops/backups/{name}/download", headers=h)
        assert resp.status_code in (400, 404), name
        assert "passwd" not in resp.text  # 不泄路径
    assert list(tmp_path.glob("bk/*")) == []  # 不落盘


def test_run_task_failure_writes_audit(client, db, monkeypatch):
    from invoicing.models import AuditLog

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("invoicing.ops.instrumentation.run_manual", _boom)
    token = _login(client, db, "admin2", Role.admin.value)
    resp = client.post("/api/v1/ops/tasks/ops_backup/run",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 500
    logs = db.query(AuditLog).filter(AuditLog.action == "OPS_TASK_RUN").all()
    assert len(logs) == 1
    assert logs[0].detail["outcome"] == "error"
    assert logs[0].detail["error"] == "RuntimeError"


def test_run_backup_failure_writes_audit(client, db, monkeypatch):
    from invoicing.models import AuditLog

    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr("invoicing.ops.backup.create_backup", _boom)
    token = _login(client, db, "admin2", Role.admin.value)
    resp = client.post("/api/v1/ops/backups/run", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 500
    logs = db.query(AuditLog).filter(AuditLog.action == "OPS_BACKUP_RUN").all()
    assert len(logs) == 1
    assert logs[0].detail["outcome"] == "error"
    assert logs[0].detail["error"] == "RuntimeError"


def test_run_backup_success_writes_audit(client, db, tmp_path, monkeypatch):
    from pathlib import Path

    from invoicing.models import AuditLog

    fake = tmp_path / "invoiceease-backup-20260917-021700.tar.gz"
    fake.write_bytes(b"fake")
    monkeypatch.setattr("invoicing.ops.backup.create_backup", lambda: fake)
    token = _login(client, db, "admin2", Role.admin.value)
    resp = client.post("/api/v1/ops/backups/run", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["name"] == fake.name
    logs = db.query(AuditLog).filter(AuditLog.action == "OPS_BACKUP_RUN").all()
    assert len(logs) == 1
    assert logs[0].detail["file"] == fake.name


def test_logs_tail_and_download(client, db, tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "invoicing.log").write_text("line1\nline2\nline3\n", encoding="utf-8")
    monkeypatch.setattr("invoicing.config.settings.log_dir", str(log_dir))
    token = _login(client, db, "admin2", Role.admin.value)
    h = {"Authorization": f"Bearer {token}"}
    resp = client.get("/api/v1/ops/logs/tail", headers=h)
    assert resp.status_code == 200
    assert resp.json()["lines"] == ["line1\n", "line2\n", "line3\n"]
    resp = client.get("/api/v1/ops/logs/download", headers=h)
    assert resp.status_code == 200
    assert resp.text == "line1\nline2\nline3\n"
