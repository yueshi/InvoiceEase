from datetime import date
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


def _seed(db, username, role, password="pass123"):
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    return user


def _login(client, username):
    return client.post("/api/v1/auth/login", json={"username": username, "password": "pass123"}).json()[
        "access_token"
    ]


def _invoice(db, **kw):
    inv = Invoice(
        file_url="a.xml",
        file_type="XML",
        invoice_number=kw.get("invoice_number", "24312000000012345678"),
        status=kw.get("status", "pending_submit"),
        total_amount=kw.get("total_amount", Decimal("1000.00")),
        seller_name=kw.get("seller_name", "示例科技有限公司"),
        issue_date=kw.get("issue_date", date(2026, 8, 1)),
        parse_source="XML",
        confidence_score=1.0,
        verify_status="passed",
        email_message_id=kw.get("email_message_id"),
    )
    db.add(inv)
    db.flush()
    return inv


def test_list_invoices_finance_sees_all(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    _invoice(db)
    token = _login(client, "caiwu1")
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_list_invoices_employee_sees_only_own(client, db):
    emp = _seed(db, "zhangsan", Role.employee.value)
    # 查重唯一键（发票代码+发票号码）约束：本测试需两张发票，号码须不同，否则唯一索引冲突
    _invoice(db, invoice_number="24312000000012345679")  # user_id 为空，员工不可见
    own = _invoice(db, email_message_id="<own@x.com>")
    own.user_id = emp.id
    db.flush()
    token = _login(client, "zhangsan")
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    items = resp.json()["items"]
    assert items[0]["id"] == own.id


def test_get_invoice_detail(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = _invoice(db)
    token = _login(client, "caiwu1")
    resp = client.get(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["invoice_number"] == "24312000000012345678"


def test_review_approve(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review")
    token = _login(client, "caiwu1")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "approve", "note": "核对无误"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_submit"
    assert resp.json()["review_note"] == "核对无误"


def test_review_reject(client, db):
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review")
    token = _login(client, "caiwu1")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "reject", "note": "信息不符"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_review_forbidden_for_employee(client, db):
    _seed(db, "zhangsan", Role.employee.value)
    inv = _invoice(db, status="pending_review")
    token = _login(client, "zhangsan")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "approve", "note": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_download_file_chinese_filename(client, db, monkeypatch):
    _seed(db, "caiwu1", Role.finance_staff.value)
    token = _login(client, "caiwu1")
    inv = Invoice(
        file_url="tenant-default/mailbox-1/1-发票.pdf",
        file_type="PDF",
        invoice_number="24312000000044444444",
    )
    db.add(inv)
    db.flush()

    class FakeStorage:
        def get(self, key):
            return b"%PDF-1.4"

    monkeypatch.setattr("invoicing.api.invoices.get_storage", lambda: FakeStorage())
    resp = client.get(
        f"/api/v1/invoices/{inv.id}/file",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert "filename*" in resp.headers["content-disposition"]


def test_list_invoices_with_list_validation_errors(client, db):
    """回归：validation_errors 为 list[dict]（写入方形态）时列表不得 500。"""
    _seed(db, "caiwu1", Role.finance_staff.value)
    inv = Invoice(
        file_url="a.xml",
        file_type="XML",
        invoice_number="24312000000055555555",
        status="pending_review",
        parse_source="PDF_UNSTRUCTURED",
        confidence_score=0.0,
        validation_errors=[{"code": "XML_PARSE_ERROR", "message": "不是数电票 XML"}],
    )
    db.add(inv)
    db.flush()
    token = _login(client, "caiwu1")
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["validation_errors"] == [{"code": "XML_PARSE_ERROR", "message": "不是数电票 XML"}]


def test_put_invoice_updates_fields_and_audits(client, db):
    from invoicing.models import AuditLog

    _seed(db, "caiwu2", Role.finance_staff.value)
    inv = _invoice(db)
    token = _login(client, "caiwu2")
    resp = client.put(
        f"/api/v1/invoices/{inv.id}",
        json={"total_amount": "999.99", "seller_name": "更正后的销售方", "review_note": "复核修正"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total_amount"] == "999.99"
    assert resp.json()["seller_name"] == "更正后的销售方"
    db.flush()
    logs = db.query(AuditLog).filter(AuditLog.action == "INVOICE_UPDATE").all()
    assert len(logs) == 1
    assert logs[0].invoice_id == inv.id
    assert "total_amount" in logs[0].detail.get("changed", {})


def test_put_invoice_forbidden_for_employee(client, db):
    _seed(db, "yuangong", Role.employee.value)
    inv = _invoice(db)
    token = _login(client, "yuangong")
    resp = client.put(
        f"/api/v1/invoices/{inv.id}",
        json={"review_note": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_delete_invoice_snapshots_audit_and_removes(client, db):
    from invoicing.models import AuditLog

    _seed(db, "zhuguan", Role.finance_manager.value)
    inv = _invoice(db)
    token = _login(client, "zhuguan")
    resp = client.delete(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    db.flush()
    assert db.get(Invoice, inv.id) is None
    logs = db.query(AuditLog).filter(AuditLog.action == "INVOICE_DELETE").all()
    assert len(logs) == 1
    assert logs[0].detail["snapshot"]["invoice_number"] == "24312000000012345678"


def test_delete_invoice_forbidden_for_finance_staff(client, db):
    _seed(db, "caiwu3", Role.finance_staff.value)
    inv = _invoice(db)
    token = _login(client, "caiwu3")
    resp = client.delete(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    db.flush()
    assert db.get(Invoice, inv.id) is not None
