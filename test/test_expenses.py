"""报销 P0 测试：一票一报、验真门槛、整票校验、员工可选范围、审批流转、无票支出凭证。"""
from datetime import date
from decimal import Decimal

import pytest

from invoicing.models import (
    BankReceipt,
    ExpenseClaim,
    ExpenseClaimStatus,
    ExpenseItem,
    Invoice,
    Role,
    User,
)
from invoicing.security import hash_password
from invoicing.workflow import expenses as svc


@pytest.fixture()
def users(db):
    emp = User(username="emp1", password_hash=hash_password("pass123"), role=Role.employee.value)
    other = User(username="emp2", password_hash=hash_password("pass123"), role=Role.employee.value)
    fin = User(username="fin1", password_hash=hash_password("pass123"), role=Role.finance_staff.value)
    admin = User(username="admin1", password_hash=hash_password("pass123"), role=Role.admin.value)
    db.add_all([emp, other, fin, admin])
    db.commit()
    # MCP 通道解析真实管理员作为报销申请人（合成 id=0 会违反外键）
    return {"emp": emp, "other": other, "fin": fin, "admin": admin}


def _invoice(db, number="24312000000012345678", **kw):
    inv = Invoice(
        file_url=f"{number}.xml", file_type="XML", invoice_number=number,
        status=kw.get("status", "pending_submit"),
        verify_status=kw.get("verify_status", "passed"),
        total_amount=kw.get("total_amount", Decimal("100.00")),
        issue_date=kw.get("issue_date", date(2026, 6, 1)),
        user_id=kw.get("user_id"),
        seller_name=kw.get("seller_name", "某某公司"),
    )
    db.add(inv)
    db.commit()
    return inv


def _claim_with_entry(db, user, title="测试报销", entry_type="other", entry_title="默认事项", **scene):
    claim = svc.create_claim(db, user, title=title)
    entry = svc.create_entry(db, user, claim.id, entry_type, entry_title, scene_fields=scene or None)
    return claim, entry


def test_claim_draft_add_invoice_and_totals(db, users):
    inv = _invoice(db, user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="6 月差旅报销")

    assert claim.claim_no.startswith("FY-")
    assert claim.status == ExpenseClaimStatus.DRAFT

    svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="travel", note="高铁票")
    db.refresh(claim)
    db.refresh(entry)
    assert claim.total_amount == Decimal("100.00")
    assert entry.amount == Decimal("100.00")  # 事项金额 = 凭证合计
    db.refresh(inv)
    assert inv.reimbursement_status == "pending"  # 占用中

    with pytest.raises(ValueError, match="已在本报销单"):
        svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id)


def test_one_invoice_one_claim_across_claims(db, users):
    """跨报销单占用：另一单再占用同一张票 → 拒绝（一票一报核心）。"""
    inv = _invoice(db, user_id=None)  # 公共池
    c1, e1 = _claim_with_entry(db, users["emp"], title="A")
    svc.add_invoice(db, users["emp"], c1.id, inv.id, e1.id, expense_type="office")

    c2, e2 = _claim_with_entry(db, users["other"], title="B")
    with pytest.raises(ValueError, match="已被"):
        svc.add_invoice(db, users["other"], c2.id, inv.id, e2.id, expense_type="office")


def test_verify_required_and_blocked_rejected(db, users):
    unverified = _invoice(db, number="24312000000000000001", verify_status="pending")
    blocked = _invoice(db, number="24312000000000000002", status="blocked")
    claim, entry = _claim_with_entry(db, users["emp"], title="X")
    with pytest.raises(ValueError, match="验真"):
        svc.add_invoice(db, users["emp"], claim.id, unverified.id, entry.id, expense_type="other")
    with pytest.raises(ValueError, match="拦截"):
        svc.add_invoice(db, users["emp"], claim.id, blocked.id, entry.id, expense_type="other")


def test_employee_scope_only_own_or_public(db, users):
    """员工只能选本人上传的或公共池（user_id 为空）的发票。"""
    mine = _invoice(db, number="24312000000000000003", user_id=users["emp"].id)
    others = _invoice(db, number="24312000000000000004", user_id=users["other"].id)
    public = _invoice(db, number="24312000000000000005", user_id=None)

    eligible = {i.id for i in svc.eligible_invoices(db, users["emp"])}
    assert mine.id in eligible and public.id in eligible
    assert others.id not in eligible

    claim, entry = _claim_with_entry(db, users["emp"], title="范围")
    with pytest.raises(ValueError, match="无权|不可报销"):
        svc.add_invoice(db, users["emp"], claim.id, others.id, entry.id, expense_type="other")


def test_submit_approve_flow_marks_invoice_claimed(db, users):
    inv = _invoice(db, user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="提交与审批")
    svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="travel")
    svc.submit_claim(db, users["emp"], claim.id)
    db.refresh(claim)
    assert claim.status == ExpenseClaimStatus.PENDING
    assert claim.submitted_at is not None

    svc.approve_claim(db, users["fin"], claim.id)
    db.refresh(claim)
    db.refresh(inv)
    assert claim.status == ExpenseClaimStatus.APPROVED and claim.approver_id == users["fin"].id
    assert inv.reimbursement_status == "claimed"  # 已报销


def test_reject_releases_invoice_and_item(db, users):
    inv = _invoice(db, user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="驳回释放")
    svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="travel")
    svc.submit_claim(db, users["emp"], claim.id)
    svc.reject_claim(db, users["fin"], claim.id, reason="发票抬头不符")

    db.refresh(claim)
    db.refresh(inv)
    assert claim.status == ExpenseClaimStatus.REJECTED
    assert claim.rejected_reason == "发票抬头不符"
    assert inv.reimbursement_status == "none"  # 释放，可重新报销
    item = db.query(ExpenseItem).filter(ExpenseItem.claim_id == claim.id).one()
    assert item.active is False

    # 释放后可再次报销（新单 + 新事项）
    c2, e2 = _claim_with_entry(db, users["emp"], title="重新报")
    svc.add_invoice(db, users["emp"], c2.id, inv.id, e2.id, expense_type="travel")


def test_withdraw_only_own_draft_or_pending(db, users):
    inv = _invoice(db, user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="撤回")
    svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="travel")
    svc.submit_claim(db, users["emp"], claim.id)
    svc.withdraw_claim(db, users["emp"], claim.id)
    db.refresh(claim)
    db.refresh(inv)
    assert claim.status == ExpenseClaimStatus.WITHDRAWN
    assert inv.reimbursement_status == "none"

    c2, _ = _claim_with_entry(db, users["emp"], title="不可撤")
    with pytest.raises(ValueError, match="无权|本人"):
        svc.withdraw_claim(db, users["other"], c2.id)


def test_scene_required_fields_enforced(db, users):
    """场景必填：差旅按子类校验（交通/住宿/市内交通/补助）；招待需对象+人数。"""
    claim = svc.create_claim(db, users["emp"], title="场景校验")

    # 差旅必须选子类
    with pytest.raises(ValueError, match="子类"):
        svc.create_entry(db, users["emp"], claim.id, "travel", "北京出差", scene_fields={})

    # 交通：方式 + 出发/到达城市 + 日期
    with pytest.raises(ValueError, match="交通方式"):
        svc.create_entry(
            db, users["emp"], claim.id, "travel", "上海→北京 高铁",
            scene_fields={"subtype": "transport", "from_city": "上海", "to_city": "北京"},
        )
    ok_transport = svc.create_entry(
        db, users["emp"], claim.id, "travel", "上海→北京 高铁",
        scene_fields={"subtype": "transport", "transport_mode": "高铁",
                      "from_city": "上海", "to_city": "北京",
                      "vehicle_no": "G10", "travel_date": "2026-06-10"},
    )
    assert ok_transport.id is not None

    # 住宿：城市 + 入住/离店
    with pytest.raises(ValueError, match="离店日期"):
        svc.create_entry(
            db, users["emp"], claim.id, "travel", "北京住宿",
            scene_fields={"subtype": "accommodation", "city": "北京", "checkin": "2026-06-10"},
        )
    ok_stay = svc.create_entry(
        db, users["emp"], claim.id, "travel", "北京住宿 3 晚",
        scene_fields={"subtype": "accommodation", "city": "北京",
                      "checkin": "2026-06-10", "checkout": "2026-06-13",
                      "nights": "3", "rooms": "1"},
    )
    assert ok_stay.id is not None

    # 市内交通：城市 + 日期；伙食补助：天数
    svc.create_entry(db, users["emp"], claim.id, "travel", "北京打车",
                     scene_fields={"subtype": "local_transport", "city": "北京",
                                   "travel_date": "2026-06-11"})
    with pytest.raises(ValueError, match="补助天数"):
        svc.create_entry(db, users["emp"], claim.id, "travel", "伙食补助",
                         scene_fields={"subtype": "allowance"})
    svc.create_entry(db, users["emp"], claim.id, "travel", "伙食补助 4 天",
                     scene_fields={"subtype": "allowance", "days": "4", "daily_standard": "100"})

    # 招待仍需对象与人数
    with pytest.raises(ValueError, match="招待对象"):
        svc.create_entry(db, users["emp"], claim.id, "entertainment", "客户晚宴", scene_fields={})


def test_submit_requires_each_entry_has_items(db, users):
    """提交前置：每个事项都必须有凭证（不允许空事项）。"""
    claim, entry = _claim_with_entry(db, users["emp"], title="空事项")
    with pytest.raises(ValueError, match="没有关联凭证"):
        svc.submit_claim(db, users["emp"], claim.id)
    claim2 = svc.create_claim(db, users["emp"], title="无事项")
    with pytest.raises(ValueError, match="没有事项"):
        svc.submit_claim(db, users["emp"], claim2.id)


def test_no_invoice_expense_bank_receipt(db, users):
    """无票支出：银行回单作为明细（金额取自回单）。"""
    r = BankReceipt(file_url="r.pdf", file_type="PDF", counterparty_name="银行",
                    amount=Decimal("899.00"), trade_date=date(2026, 5, 12), status="unmatched")
    db.add(r)
    db.commit()
    claim, entry = _claim_with_entry(db, users["emp"], title="手续费", entry_type="office",
                                     entry_title="账户管理费")
    svc.add_receipt(db, users["emp"], claim.id, r.id, entry.id, voucher_type="bank_receipt",
                    expense_type="office", note="账户管理费")
    db.refresh(claim)
    assert claim.total_amount == Decimal("899.00")
    item = db.query(ExpenseItem).filter(ExpenseItem.claim_id == claim.id).one()
    assert item.voucher_type == "bank_receipt" and item.deductible is True


def test_receipt_voucher_requires_payee_elements(db, users):
    """收款凭证（小额零星个人）：缺姓名/身份证号 → 不可税前扣除（28 号公告要素）。"""
    claim, entry = _claim_with_entry(db, users["emp"], title="零星采购", entry_type="office",
                                     entry_title="工地买菜")
    item = svc.add_manual_voucher(
        db, users["emp"], claim.id, entry.id, voucher_type="receipt_voucher",
        amount=Decimal("480.00"), expense_type="office", note="工地买菜",
        payee_name=None, payee_id_no=None,
    )
    assert item.deductible is False
    assert "身份证" in (item.deductible_note or "") or "姓名" in (item.deductible_note or "")

    claim2, entry2 = _claim_with_entry(db, users["emp"], title="零星采购2", entry_type="office")
    ok = svc.add_manual_voucher(
        db, users["emp"], claim2.id, entry2.id, voucher_type="receipt_voucher",
        amount=Decimal("480.00"), expense_type="office",
        payee_name="张三", payee_id_no="110101199001011234",
    )
    assert ok.deductible is True


def test_over_threshold_hints_invoice_needed(db, users):
    """超小额零星阈值（默认 500）→ 提示需取得发票（标记不可扣除 + 说明）。"""
    claim, entry = _claim_with_entry(db, users["emp"], title="超标", entry_type="office")
    item = svc.add_manual_voucher(
        db, users["emp"], claim.id, entry.id, voucher_type="receipt_voucher",
        amount=Decimal("800.00"), expense_type="office",
        payee_name="张三", payee_id_no="110101199001011234",
    )
    assert item.deductible is False
    assert "500" in (item.deductible_note or "") or "发票" in (item.deductible_note or "")


def test_approve_requires_pending_and_finance_role(db, users):
    inv = _invoice(db, user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="越权审批")
    svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="travel")
    with pytest.raises(ValueError, match="待审批"):
        svc.approve_claim(db, users["fin"], claim.id)  # 草稿不可审批
    svc.submit_claim(db, users["emp"], claim.id)
    with pytest.raises(ValueError, match="财务"):
        svc.approve_claim(db, users["emp"], claim.id)  # 员工不可审批


# ---- API 层 ---------------------------------------------------------------


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient

    from invoicing.db import get_db
    from invoicing.main import create_app

    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def _login(client, username, password="pass123"):
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_expense_api_full_flow(client, db, users):
    """API 全链路：建单 → 选票池 → 加票 → 提交 → 财务审批 → 发票标记已报销。"""
    from invoicing.models import ExpenseClaimStatus

    emp, fin = _login(client, "emp1"), _login(client, "fin1")
    inv = _invoice(db, number="24312000000000000009", user_id=None, total_amount=Decimal("266.00"))

    # 可选票池（本人 + 公共池，未占用）
    pool = client.get("/api/v1/expenses/eligible-invoices", headers=emp).json()
    assert any(i["id"] == inv.id for i in pool)

    claim = client.post("/api/v1/expenses", headers=emp, json={"title": "6 月打车"}).json()
    assert claim["claim_no"].startswith("FY-")

    # 建事项（差旅需城市+起止日期）→ 再挂票
    entry = client.post(
        f"/api/v1/expenses/{claim['id']}/entries", headers=emp,
        json={"entry_type": "travel", "title": "北京机场往返打车", "occurred_on": "2026-06-12",
              "scene_fields": {"subtype": "local_transport", "city": "北京",
                               "travel_date": "2026-06-12"}},
    )
    assert entry.status_code == 200, entry.text
    entry_id = entry.json()["id"]

    # 差旅子类选择错误/要素缺失 → 422（场景必填校验）
    bad_entry = client.post(
        f"/api/v1/expenses/{claim['id']}/entries", headers=emp,
        json={"entry_type": "travel", "title": "缺子类", "scene_fields": {}},
    )
    assert bad_entry.status_code == 422 and "子类" in bad_entry.json()["detail"]

    # 交通子类缺城市 → 422
    bad_transport = client.post(
        f"/api/v1/expenses/{claim['id']}/entries", headers=emp,
        json={"entry_type": "travel", "title": "缺城市",
              "scene_fields": {"subtype": "transport", "transport_mode": "高铁"}},
    )
    assert bad_transport.status_code == 422 and "出发城市" in bad_transport.json()["detail"]

    added = client.post(
        f"/api/v1/expenses/{claim['id']}/entries/{entry_id}/invoices", headers=emp,
        json={"invoice_id": inv.id, "expense_type": "travel", "note": "机场往返"},
    )
    assert added.status_code == 200, added.text

    # 占用后票池不再出现该票
    pool2 = client.get("/api/v1/expenses/eligible-invoices", headers=emp).json()
    assert all(i["id"] != inv.id for i in pool2)

    detail = client.get(f"/api/v1/expenses/{claim['id']}", headers=emp).json()
    assert detail["claim"]["total_amount"] == "266.00" and len(detail["items"]) == 1
    assert len(detail["entries"]) == 1
    assert detail["entries"][0]["amount"] == "266.00"  # 事项金额 = 凭证合计
    assert len(detail["entries"][0]["items"]) == 1

    sub = client.post(f"/api/v1/expenses/{claim['id']}/submit", headers=emp)
    assert sub.json()["status"] == ExpenseClaimStatus.PENDING.value

    # 员工不可审批（403）；财务可审批
    assert client.post(f"/api/v1/expenses/{claim['id']}/approve", headers=emp).status_code == 403
    ok = client.post(f"/api/v1/expenses/{claim['id']}/approve", headers=fin)
    assert ok.status_code == 200 and ok.json()["status"] == ExpenseClaimStatus.APPROVED.value

    db.refresh(inv)
    assert inv.reimbursement_status == "claimed"

    # 财务可见全部；员工只看本人的
    assert len(client.get("/api/v1/expenses", headers=fin).json()) == 1
    other = _login(client, "emp2")
    assert client.get("/api/v1/expenses", headers=other).json() == []


def test_expense_api_no_invoice_voucher(client, db, users):
    """无票支出：收款凭证要素不全 → 返回不可税前扣除标记（API 透出）。"""
    emp = _login(client, "emp1")
    claim = client.post("/api/v1/expenses", headers=emp, json={"title": "零星"}).json()
    entry = client.post(
        f"/api/v1/expenses/{claim['id']}/entries", headers=emp,
        json={"entry_type": "office", "title": "工地零星采购"},
    ).json()
    resp = client.post(
        f"/api/v1/expenses/{claim['id']}/entries/{entry['id']}/vouchers", headers=emp,
        json={"voucher_type": "receipt_voucher", "amount": "480.00", "expense_type": "office",
              "note": "工地买菜"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deductible"] is False and "身份证" in (body["deductible_note"] or "")


def test_expense_api_other_person_claim_hidden(client, db, users):
    """越权：他人报销单详情 → 403。"""
    emp, other = _login(client, "emp1"), _login(client, "emp2")
    claim = client.post("/api/v1/expenses", headers=emp, json={"title": "私有"}).json()
    assert client.get(f"/api/v1/expenses/{claim['id']}", headers=other).status_code == 403


# ---- MCP 工具 -------------------------------------------------------------


def test_mcp_expense_tools_full_flow(db, users):
    """WorkBuddy 对话报销链路：建单 → 批量加票（含失败项）→ 提交 → 审批。"""
    from invoicing.mcp import tools as mt

    inv1 = _invoice(db, number="24312000000000000021", user_id=None, total_amount=Decimal("120.00"))
    inv2 = _invoice(db, number="24312000000000000022", user_id=None, total_amount=Decimal("80.00"))
    bad = _invoice(db, number="24312000000000000023", user_id=None, verify_status="pending")

    pool = mt.expense_eligible_invoices()
    assert {i["id"] for i in pool} >= {inv1.id, inv2.id}
    assert all(i["id"] != bad.id for i in pool)  # 未验真不可选

    claim = mt.expense_create("6 月差旅", remark="高铁+打车")
    entry = mt.expense_add_entry(
        claim["id"], "travel", "上海→北京 高铁", occurred_on="2026-06-10",
        scene_fields={"subtype": "transport", "transport_mode": "高铁",
                      "from_city": "上海", "to_city": "北京",
                      "vehicle_no": "G10", "travel_date": "2026-06-10"},
    )
    res = mt.expense_add_invoices(
        claim["id"], entry["entry_id"],
        ["24312000000000000021", "24312000000000000022", "24312000000000000023", "不存在的号"],
        expense_type="travel",
    )
    assert res["total_amount"] == "200.00"
    ok = [r for r in res["results"] if r["success"]]
    assert len(ok) == 2
    assert any("验真" in (r.get("error") or "") for r in res["results"])
    assert any("不存在" in (r.get("error") or "") for r in res["results"])

    submitted = mt.expense_submit(claim["id"])
    assert submitted["status"] == "pending_approval"

    approved = mt.expense_approve(claim["id"], action="approve")
    assert approved["status"] == "approved"

    listed = mt.expense_list(status="approved")
    row = next(c for c in listed if c["claim_no"] == claim["claim_no"])
    assert row["item_count"] == 2
    assert row["entries"][0]["title"] == "上海→北京 高铁"  # 事项级摘要（Agent 可汇报）
    assert row["entries"][0]["amount"] == "200.00"
