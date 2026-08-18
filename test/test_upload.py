"""员工交票上传测试（M3：合规引导 + 归属 + 进度）。"""
import io

import pytest
from fastapi.testclient import TestClient

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import AuditLog, Invoice, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_parse(monkeypatch):
    """上传后解析/验真管线 no-op：本测试聚焦上传行为（归属/审计/合规引导）。"""
    import invoicing.workers.queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_parse_sync", lambda invoice_id: None)


def _seed(db, username, role, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def _login(client, username):
    return client.post("/api/v1/auth/login", json={"username": username, "password": "pass123"}).json()[
        "access_token"
    ]


def _upload(client, token, filename, content, content_type="application/octet-stream"):
    return client.post(
        "/api/v1/invoices/upload",
        files={"file": (filename, io.BytesIO(content), content_type)},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_employee_uploads_pdf_and_owns_it(client, db):
    """员工上传 PDF → 入库且 user_id 归属上传者 + INVOICE_UPLOAD 审计。"""
    emp = _seed(db, "yuangong1", Role.employee.value)
    token = _login(client, "yuangong1")
    resp = _upload(client, token, "fapiao.pdf", b"%PDF-1.4 test", "application/pdf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_type"] == "PDF"
    assert body["user_id"] == emp.id
    assert body["email_subject"] == "员工上传"
    db.flush()
    inv = db.query(Invoice).filter(Invoice.user_id == emp.id).first()
    assert inv is not None
    logs = db.query(AuditLog).filter(AuditLog.action == "INVOICE_UPLOAD").all()
    assert len(logs) == 1
    assert logs[0].user_id == emp.id


def test_image_upload_rejected_with_guidance(client, db):
    """图片上传 422 + 合规引导（原件走邮箱）。"""
    _seed(db, "yuangong2", Role.employee.value)
    token = _login(client, "yuangong2")
    resp = _upload(client, token, "photo.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")
    assert resp.status_code == 422
    assert "邮箱" in resp.json()["detail"]


def test_upload_requires_auth(client):
    resp = client.post(
        "/api/v1/invoices/upload",
        files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )
    assert resp.status_code == 401
