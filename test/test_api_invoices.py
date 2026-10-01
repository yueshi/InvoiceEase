from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from invoicing.storage import LocalFileStorage

from invoicing.db import get_db
from invoicing.main import create_app
from invoicing.models import Invoice, Role, User
from invoicing.security import hash_password


@pytest.fixture()
def storage(tmp_path):
    return LocalFileStorage(root=str(tmp_path / "originals"))


@pytest.fixture(autouse=True)
def _point_api_at_fixture_storage(monkeypatch, storage):
    # 下载/预览端点经 get_storage() 读原件，测试中指向隔离的 tmp_path 存储
    monkeypatch.setattr("invoicing.api.invoices.get_storage", lambda: storage)


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
    assert resp.headers["content-disposition"].startswith("inline;")  # PDF 内联预览（2026-08-16 变更）
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


def _ofd_invoice(db, storage):
    import io
    import zipfile

    from invoicing.parse import ofd_render  # noqa: F401  确保模块可导入

    content = """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Area>
    <ofd:PhysicalBox>0 0 210 297</ofd:PhysicalBox>
    <ofd:ApplicationBox>0 0 210 297</ofd:ApplicationBox>
  </ofd:Area>
  <ofd:Content>
    <ofd:Layer>
      <ofd:DrawParam ID="2" FillColor="0 0 0"/>
      <ofd:PathObject Boundary="0 0 210 297" Fill="true" DrawParam="2" ID="4">
        <ofd:AbbreviatedData>M 20 20 L 100 20 L 100 60 L 20 60 C</ofd:AbbreviatedData>
      </ofd:PathObject>
    </ofd:Layer>
  </ofd:Content>
</ofd:Page>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", content)
    key = "test-preview/vectors.ofd"
    storage.put(key, buf.getvalue(), "application/ofd")
    inv = Invoice(file_url=key, file_type="OFD", status="parsed", invoice_number="24312000000012345678")
    db.add(inv)
    db.flush()
    return inv


def test_preview_ofd_renders_png(client, db, storage):
    _seed(db, "caiwu4", Role.finance_staff.value)
    inv = _ofd_invoice(db, storage)
    token = _login(client, "caiwu4")
    resp = client.get(f"/api/v1/invoices/{inv.id}/preview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_ofd_unrenderable_422(client, db, storage):
    _seed(db, "caiwu5", Role.finance_staff.value)
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", "<ofd:Page/>")
    key = "test-preview/empty.ofd"
    storage.put(key, buf.getvalue(), "application/ofd")
    inv = Invoice(file_url=key, file_type="OFD", status="parsed", invoice_number="24312000000012345678")
    db.add(inv)
    db.flush()
    token = _login(client, "caiwu5")
    resp = client.get(f"/api/v1/invoices/{inv.id}/preview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_file_pdf_inline_disposition(client, db, storage):
    _seed(db, "caiwu6", Role.finance_staff.value)
    storage.put("test-preview/doc.pdf", b"%PDF-1.4 x", "application/pdf")
    inv = Invoice(file_url="test-preview/doc.pdf", file_type="PDF", status="parsed",
                  invoice_number="24312000000012345678")
    db.add(inv)
    db.flush()
    token = _login(client, "caiwu6")
    resp = client.get(f"/api/v1/invoices/{inv.id}/file", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")


def test_unblock_clears_duplicate_and_goes_review(client, db):
    from invoicing.models import AuditLog

    _seed(db, "caiwu7", Role.finance_staff.value)
    # C5 修复：PRAGMA foreign_keys=ON 后 duplicate_of_id 必须指向真实存在的发票
    original = _invoice(db, status="parsed", invoice_number="24312000000070000001")
    inv = _invoice(db, status="blocked", invoice_number="24312000000070000002")
    inv.duplicate_flag = True
    inv.duplicate_of_id = original.id
    db.flush()
    token = _login(client, "caiwu7")
    resp = client.post(f"/api/v1/invoices/{inv.id}/unblock", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_review"
    assert resp.json()["duplicate_flag"] is False
    assert resp.json()["duplicate_of_id"] is None
    db.flush()
    logs = db.query(AuditLog).filter(AuditLog.action == "UNBLOCK").all()
    assert len(logs) == 1


def test_unblock_non_blocked_409(client, db):
    _seed(db, "caiwu8", Role.finance_staff.value)
    inv = _invoice(db, status="parsed")
    token = _login(client, "caiwu8")
    resp = client.post(f"/api/v1/invoices/{inv.id}/unblock", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409


def test_delete_clears_dangling_duplicate_refs(client, db):
    _seed(db, "zhuguan2", Role.finance_manager.value)
    victim = _invoice(db)
    dep = _invoice(db, invoice_number="24312000000099999999", status="blocked")
    dep.duplicate_flag = True
    dep.duplicate_of_id = victim.id
    db.flush()
    token = _login(client, "zhuguan2")
    resp = client.delete(f"/api/v1/invoices/{victim.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    db.flush()
    db.refresh(dep)
    assert dep.duplicate_flag is False
    assert dep.duplicate_of_id is None
    assert dep.status == "pending_review"  # 被拦截的依赖票转待复核


def test_delete_resets_paired_receipts_to_unmatched(client, db):
    """删票必须复位指向它的回单（终审 1a）。

    回归：FK `ON DELETE SET NULL` 只清 paired_invoice_id，status 停在 "paired"
    → 回单进了催票队列（未配对）却顶着绿色「已配对」，同屏自相矛盾。
    """
    from invoicing.models import BankReceipt
    from invoicing.workflow.receipts import is_unmatched_expense

    _seed(db, "zhuguan4", Role.finance_manager.value)
    inv = _invoice(db)
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", trade_date=date(2026, 8, 5),
        counterparty_name="供应商甲", amount=Decimal("800.00"),
        direction="付", category="purchase", category_source="rule",
        paired_invoice_id=inv.id, status="paired",
    )
    db.add(r)
    db.flush()
    token = _login(client, "zhuguan4")
    resp = client.delete(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    db.flush()
    db.refresh(r)
    assert r.paired_invoice_id is None
    assert r.status == "unmatched"
    assert is_unmatched_expense(r) is True  # 回归催票队列，且状态不再自相矛盾


def test_cleanup_dependents_keeps_receipts_of_surviving_invoice(db):
    """发票保留的清理（重复拦截路径）不得拆有效配对——unlink_receipts 只在删除前传。"""
    from invoicing.models import BankReceipt
    from invoicing.workflow.services import _cleanup_dependents_of

    inv = _invoice(db)
    r = BankReceipt(
        file_url="r.pdf", file_type="PDF", counterparty_name="供应商甲",
        amount=Decimal("800.00"), direction="付", category="purchase",
        paired_invoice_id=inv.id, status="paired",
    )
    db.add(r)
    db.flush()
    assert _cleanup_dependents_of(db, inv.id) == 0
    assert (r.paired_invoice_id, r.status) == (inv.id, "paired")


def test_put_amount_fields_recalculates_validation_errors(client, db):
    """回归：用户修正大写金额后 validation_errors 重算，CN_MISMATCH 告警消除（飞猪票事故）。"""
    _seed(db, "caiwu9", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review", total_amount=Decimal("364.70"))
    inv.amount_without_tax = Decimal("364.70")
    inv.tax_amount = Decimal("0.00")
    inv.total_amount_cn = "叁佰陆拾肆元柒角"  # 源文件瑕疵：缺「整」
    inv.validation_errors = [
        {"code": "CN_MISMATCH", "message": "大小写金额不一致: 364.70 对应 '叁佰陆拾肆元柒角整'，实际 '叁佰陆拾肆元柒角'"}
    ]
    db.flush()
    token = _login(client, "caiwu9")
    resp = client.put(
        f"/api/v1/invoices/{inv.id}",
        json={"total_amount_cn": "叁佰陆拾肆元柒角整"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["validation_errors"] is None  # 用户修正值参与校验，告警消除


def test_put_key_fields_clears_ai_review_prediction(client, db):
    """B4 回归：修正金额/购销方后旧预判失效，理由不得基于旧数据。"""
    _seed(db, "caiwu10", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review")
    inv.ai_review_verdict = "approve"
    inv.ai_review_reason = "字段完整且校验通过"
    inv.ai_review_confidence = 1.0
    db.flush()
    token = _login(client, "caiwu10")
    resp = client.put(
        f"/api/v1/invoices/{inv.id}",
        json={"total_amount": "888.00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ai_review_verdict"] is None
    assert resp.json()["ai_review_reason"] is None
    assert resp.json()["ai_review_confidence"] is None


def test_invoice_out_flags_mock_verify(client, db):
    """A2 回归：mock 验真的票必须携带 verify_is_mock 标记（前端/数字员工据此注明模拟状态）。"""
    _seed(db, "caiwu11", Role.finance_staff.value)
    inv = _invoice(db, status="pending_submit")
    inv.verify_detail = {"reason": "mock_rule_default_pass"}
    db.flush()
    token = _login(client, "caiwu11")
    resp = client.get(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["verify_is_mock"] is True


def test_invoice_out_not_mock_without_mock_detail(client, db):
    """A2 回归：无 mock 字样（或未验真）的票 verify_is_mock=False。"""
    _seed(db, "caiwu12", Role.finance_staff.value)
    inv = _invoice(db, status="pending_submit")
    inv.verify_detail = None
    db.flush()
    token = _login(client, "caiwu12")
    resp = client.get(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["verify_is_mock"] is False


def test_review_audit_records_ai_verdict_comparison(client, db):
    """B1 回归：复核动作审计须记录当时的 AI 预判（观察期改判率数据源，P3 信任仪表盘）。"""
    from invoicing.models import AuditLog

    _seed(db, "caiwu13", Role.finance_staff.value)
    inv = _invoice(db, status="pending_review")
    inv.ai_review_verdict = "uncertain"
    inv.ai_review_confidence = 0.6
    db.flush()
    token = _login(client, "caiwu13")
    resp = client.post(
        f"/api/v1/invoices/{inv.id}/review",
        json={"action": "approve", "note": "核实无误"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    db.flush()
    logs = db.query(AuditLog).filter(AuditLog.action == "REVIEW").all()
    assert logs[-1].detail["ai_verdict"] == "uncertain"
    assert logs[-1].detail["ai_confidence"] == 0.6


def test_invoice_out_mock_flag_structured_not_text_coupled(client, db):
    """M1：结构化 mock 标志优先——mock 规则 reason 文案改动不再影响披露判定。"""
    _seed(db, "caiwu13", Role.finance_staff.value)
    inv = _invoice(db, status="pending_submit")
    inv.verify_detail = {"status": "passed", "mock": True, "reason": "文案改过了没有 mock_ 前缀"}
    db.flush()
    token = _login(client, "caiwu13")
    resp = client.get(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["verify_is_mock"] is True


def test_list_invoices_includes_submitted_by_name(client, db):
    """列表带提交人用户名；无归属（邮箱自动收取）为 None。"""
    _seed(db, "caiwu14", Role.finance_staff.value)
    emp = _seed(db, "zhangsan", Role.employee.value)
    owned = _invoice(db, invoice_number="A1000000000000000001")
    owned.user_id = emp.id
    _invoice(db, invoice_number="A1000000000000000002")  # user_id 为空：无归属
    db.flush()
    token = _login(client, "caiwu14")
    resp = client.get("/api/v1/invoices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    items = {i["invoice_number"]: i for i in resp.json()["items"]}
    assert items["A1000000000000000001"]["submitted_by_name"] == "zhangsan"
    assert items["A1000000000000000002"]["submitted_by_name"] is None


def test_get_invoice_detail_includes_submitted_by_name(client, db):
    """详情同样附挂提交人用户名（抽屉与 MCP invoice_detail 共用此路径）。"""
    _seed(db, "caiwu15", Role.finance_staff.value)
    emp = _seed(db, "lisi", Role.employee.value)
    inv = _invoice(db, invoice_number="A1000000000000000003")
    inv.user_id = emp.id
    db.flush()
    token = _login(client, "caiwu15")
    resp = client.get(f"/api/v1/invoices/{inv.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["submitted_by_name"] == "lisi"
