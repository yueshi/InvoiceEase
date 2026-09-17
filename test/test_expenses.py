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
        buyer_name=kw.get("buyer_name"),
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


def test_mcp_expense_tools_full_flow(db, users, mcp_auth):
    """WorkBuddy 对话报销链路：建单 → 批量加票（含失败项）→ 提交 → 审批。"""
    from invoicing.mcp import tools as mt

    mcp_auth(users["admin"])  # 工具层改为从认证上下文取身份，不再隐式用「最小 id 的 admin」

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


# ---- 发票自动类型标签（规则优先，未命中留空 = 未归类）----------------------


def test_rule_only_classify_returns_none_when_no_hit():
    """规则通道只给确定命中；未命中返回 None（不臆测，不走 LLM）。"""
    from invoicing.parse.classify import rule_suggest_expense_type

    assert rule_suggest_expense_type("中国国家铁路集团有限公司", None) == "travel"
    assert rule_suggest_expense_type("福建予君酒店管理有限公司", None) == "travel"
    assert rule_suggest_expense_type("某某餐饮有限公司", None) == "entertainment"
    assert rule_suggest_expense_type("办公用品商行", None) == "office"
    # 办公类扩展关键词（耗材/快递/软件/会务/物业杂费）
    for seller in ("XX耗材经营部", "顺丰速运有限公司", "某某软件科技有限公司",
                   "XX会务服务有限公司", "XX物业服务中心", "某某办公设备有限公司"):
        assert rule_suggest_expense_type(seller, None) == "office", seller
    # 办公饮用水 → 福利费（受益对象是本企业员工，与团建同口径）
    assert rule_suggest_expense_type("某某桶装水配送中心", None) == "welfare"
    assert rule_suggest_expense_type("某个没听过的科技有限公司", None) is None  # 未命中 → 留空


def test_list_invoices_filter_by_expense_type(client, db, users):
    """发票列表支持按费用类型筛选（含 unclassified=未归类）；财务账号可见全量。"""
    emp = _login(client, "fin1")
    _invoice(db, number="24312000000000000041", seller_name="铁路公司")  # 未归类
    inv2 = _invoice(db, number="24312000000000000042", seller_name="某公司")
    inv2.expense_type = "office"
    db.commit()

    office = client.get("/api/v1/invoices?expense_type=office&page=1&page_size=50", headers=emp).json()
    assert {i["invoice_number"] for i in office["items"]} == {"24312000000000000042"}

    unclassified = client.get(
        "/api/v1/invoices?expense_type=unclassified&page=1&page_size=50", headers=emp
    ).json()
    nums = {i["invoice_number"] for i in unclassified["items"]}
    assert "24312000000000000041" in nums and "24312000000000000042" not in nums


# ---- 发票自动类型标签（六类，含福利费）------------------------------------


def test_rule_only_classify_six_types():
    """规则通道：团建/聚餐 → 福利费（优先于餐饮的招待）；未命中返回 None（留空）。"""
    from invoicing.parse.classify import rule_suggest_expense_type

    assert rule_suggest_expense_type("中国国家铁路集团有限公司", None) == "travel"
    assert rule_suggest_expense_type("福建予君酒店管理有限公司", None) == "travel"
    assert rule_suggest_expense_type("某某餐饮有限公司", None) == "entertainment"
    assert rule_suggest_expense_type("某某团建拓展服务有限公司", None) == "welfare"
    assert rule_suggest_expense_type("员工聚餐（xx餐厅）", None) == "welfare"  # 团建优先于餐饮
    assert rule_suggest_expense_type("办公用品商行", None) == "office"
    # 办公类扩展关键词（耗材/快递/软件/会务/物业杂费）
    for seller in ("XX耗材经营部", "顺丰速运有限公司", "某某软件科技有限公司",
                   "XX会务服务有限公司", "XX物业服务中心", "某某办公设备有限公司"):
        assert rule_suggest_expense_type(seller, None) == "office", seller
    # 办公饮用水 → 福利费（受益对象是本企业员工，与团建同口径）
    assert rule_suggest_expense_type("某某桶装水配送中心", None) == "welfare"
    assert rule_suggest_expense_type("某不知名科技有限公司", None) is None


def test_classify_llm_prompt_requires_json_and_parses(monkeypatch):
    """回归：chat_json 走 response_format=json_object，提示词必须含 "json"，
    且返回值需按 JSON 解析（此前缺 json 字样导致 LLM 归类静默失败 → other）。"""
    from invoicing.parse import classify

    captured = {}

    class FakeEngine:
        def chat_json(self, system_prompt, user_content):
            captured["prompt"] = system_prompt
            return '{"expense_type": "welfare"}'

    monkeypatch.setattr(classify, "get_llm_engine", lambda: FakeEngine())
    assert classify.suggest_expense_type("某某服务有限公司", None) == "welfare"
    assert "json" in captured["prompt"].lower(), "提示词必须含 json 字样，否则 DeepSeek 400"


def test_claim_item_accepts_welfare_type(db, users):
    """报销明细支持福利费类型（团建/聚餐走福利费，区别于招待费口径）。"""
    inv = _invoice(db, number="24312000000000000051", user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="团建")
    item = svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="welfare")
    assert item.voucher_type == "invoice"
    assert item.expense_type == "welfare"


def test_list_invoices_filter_by_expense_type(client, db, users):
    """发票列表支持按费用类型筛选（含 unclassified=未归类）；财务账号可见全量。"""
    emp = _login(client, "fin1")
    _invoice(db, number="24312000000000000041", seller_name="铁路公司")  # 未归类
    inv2 = _invoice(db, number="24312000000000000042", seller_name="某公司")
    inv2.expense_type = "office"
    db.commit()

    office = client.get("/api/v1/invoices?expense_type=office&page=1&page_size=50", headers=emp).json()
    assert {i["invoice_number"] for i in office["items"]} == {"24312000000000000042"}

    unclassified = client.get(
        "/api/v1/invoices?expense_type=unclassified&page=1&page_size=50", headers=emp
    ).json()
    nums = {i["invoice_number"] for i in unclassified["items"]}
    assert "24312000000000000041" in nums and "24312000000000000042" not in nums


def test_delete_claim_releases_invoices_and_audits(db, users):
    """删除报销单：释放发票占用（回到 none）+ 审计快照留痕；越权被拒。"""
    from invoicing.models import AuditLog

    inv = _invoice(db, number="24312000000000000061", user_id=users["emp"].id)
    claim, entry = _claim_with_entry(db, users["emp"], title="待删除")
    svc.add_invoice(db, users["emp"], claim.id, inv.id, entry.id, expense_type="travel")
    svc.submit_claim(db, users["emp"], claim.id)
    svc.approve_claim(db, users["fin"], claim.id)
    db.refresh(inv)
    assert inv.reimbursement_status == "claimed"

    # 他人不可删
    with pytest.raises(ValueError, match="无权"):
        svc.delete_claim(db, users["other"], claim.id)

    svc.delete_claim(db, users["emp"], claim.id)
    from invoicing.models import ExpenseClaim as _C

    assert db.get(_C, claim.id) is None
    db.refresh(inv)
    assert inv.reimbursement_status == "none"  # 释放，可重新报销
    log = (
        db.query(AuditLog)
        .filter(AuditLog.action == "EXPENSE_DELETE")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert log is not None and log.detail.get("claim_no") == claim.claim_no
    assert log.detail.get("snapshot", {}).get("total_amount")


# ---- 单据类型（新建时选择）------------------------------------------------


def test_create_claim_with_type_and_entry_inherits(db, users):
    """新建报销单选择类型；事项类型未指定时跟随单据类型；非法类型拒绝。"""
    claim = svc.create_claim(db, users["emp"], title="7 月福州出差", claim_type="travel")
    assert claim.claim_type == "travel"

    # 事项未显式给类型 → 跟随单据类型
    entry = svc.create_entry(db, users["emp"], claim.id, None, "上海→福州 高铁",
                             scene_fields={"subtype": "transport", "transport_mode": "高铁",
                                           "from_city": "上海", "to_city": "福州",
                                           "travel_date": "2026-07-15"})
    assert entry.entry_type == "travel"

    # 显式指定类型则以指定为准（差旅单里也可记招待客户）
    other = svc.create_entry(
        db, users["emp"], claim.id, "entertainment", "福州客户晚宴",
        scene_fields={"guests": "客户张总", "headcount": "3"},
    )
    assert other.entry_type == "entertainment"

    with pytest.raises(ValueError, match="非法单据类型"):
        svc.create_claim(db, users["emp"], title="X", claim_type="bad_type")


def test_claim_type_filter_and_mcp(db, users, mcp_auth):
    """按单据类型筛选；MCP expense_list 带出单据类型，且**以员工身份只看到自己的单**。"""
    svc.create_claim(db, users["emp"], title="差旅单", claim_type="travel")
    svc.create_claim(db, users["emp"], title="采购单", claim_type="procurement")

    travel = svc.list_claims(db, users["emp"], claim_type="travel")
    assert {c.title for c in travel} == {"差旅单"}

    from invoicing.mcp import tools as mt

    mcp_auth(users["emp"])
    rows = mt.expense_list(claim_type="procurement")
    assert any(c["title"] == "采购单" and c["claim_type"] == "procurement" for c in rows)


def test_create_claim_api_with_type(client, db, users):
    """API：新建报销单带类型；列表与详情带出类型。"""
    emp = _login(client, "emp1")
    resp = client.post("/api/v1/expenses", headers=emp,
                       json={"title": "团建活动", "claim_type": "welfare"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_type"] == "welfare"

    listed = client.get("/api/v1/expenses?claim_type=welfare", headers=emp).json()
    assert any(c["claim_no"] == body["claim_no"] for c in listed)

    bad = client.post("/api/v1/expenses", headers=emp,
                      json={"title": "X", "claim_type": "nope"})
    assert bad.status_code == 422


# ---- 销项发票导入（外部开票）+ 红字处理 + 与回单关联 ----------------------


def test_import_sales_invoice_file_and_red_link(db, users, tmp_path):
    """导入已开票（文件）：解析入库为销项；红票自动关联原蓝票（票面原发票号）。"""
    from invoicing.workflow import sales as sales_svc

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice><SellerName>西安启智合创科技有限公司</SellerName>
<BuyerName>某某客户有限公司</BuyerName><InvoiceNumber>26617000000309516999</InvoiceNumber>
<IssueDate>2026-07-01</IssueDate><TotalAmount>1000.00</TotalAmount></Invoice>"""
    p = tmp_path / "blue.xml"
    p.write_bytes(xml.encode())

    blue = sales_svc.import_sales_files(db, users["fin"], [("blue.xml", xml.encode())])
    assert blue[0]["status"] == "imported"
    inv = db.query(Invoice).filter(Invoice.invoice_number == "26617000000309516999").one()
    assert inv.invoice_direction == "output"

    # 红字票：票面含原发票号码 → 自动关联
    red_xml = xml.replace("26617000000309516999", "26617000000309517000").replace(
        "<TotalAmount>1000.00</TotalAmount>",
        "<TotalAmount>-300.00</TotalAmount><OriginalInvoiceNumber>26617000000309516999</OriginalInvoiceNumber>",
    ).replace("<Invoice>", "<Invoice><InvoiceType>红字发票</InvoiceType>")
    red = sales_svc.import_sales_files(db, users["fin"], [("red.xml", red_xml.encode())])
    assert red[0]["status"] == "imported"
    red_inv = db.query(Invoice).filter(Invoice.invoice_number == "26617000000309517000").one()
    assert red_inv.red_flag is True
    assert red_inv.original_invoice_id == inv.id  # 自动关联原蓝票


def test_import_sales_list_csv(db, users):
    """导入已开票（清单批量）：CSV 逐行入库；重复号码跳过；红票按原发票号关联。"""
    from invoicing.workflow import sales as sales_svc

    csv_data = (
        "发票号码,开票日期,购买方名称,购买方税号,金额,税额,价税合计,发票类型,原发票号码\n"
        "26617000000309518001,2026-07-02,客户甲有限公司,91310000AAAAAAAAAA,100.00,13.00,113.00,数电票,\n"
        "26617000000309518002,2026-07-03,客户乙有限公司,91310000BBBBBBBBBB,-50.00,-6.50,-56.50,红字数电票,26617000000309518001\n"
    ).encode()
    result = sales_svc.import_sales_list(db, users["fin"], csv_data, filename="sales.csv")
    assert result["imported"] == 2
    red = db.query(Invoice).filter(Invoice.invoice_number == "26617000000309518002").one()
    blue = db.query(Invoice).filter(Invoice.invoice_number == "26617000000309518001").one()
    assert red.invoice_direction == "output" and red.red_flag is True
    assert red.original_invoice_id == blue.id

    # 幂等：同清单再导入 → 全部跳过
    again = sales_svc.import_sales_list(db, users["fin"], csv_data, filename="sales.csv")
    assert again["imported"] == 0 and again["skipped"] == 2


def test_link_red_invoice_manually(db, users):
    """人工补关联：未自动关联的红票可手动指定原蓝票；非红票/方向不符拒绝。"""
    from invoicing.workflow import sales as sales_svc

    blue = _invoice(db, number="24312000000000000071", seller_name="本司")
    red = _invoice(db, number="24312000000000000072", seller_name="本司")
    red.red_flag = True
    db.commit()

    svc = sales_svc
    svc.link_red_invoice(db, users["fin"], red.id, blue.id)
    db.refresh(red)
    assert red.original_invoice_id == blue.id

    normal = _invoice(db, number="24312000000000000073")
    with pytest.raises(ValueError, match="红字"):
        svc.link_red_invoice(db, users["fin"], normal.id, blue.id)


def test_pair_direction_aware(db, users):
    """配方向感知：收款回单 ↔ 销项票（匹配购买方）；付款回单 ↔ 进项票（匹配销售方）。"""
    from datetime import date as _date

    from invoicing.parse.receipt import suggest_pair

    # 销项票：本司开给客户甲公司
    sales_inv = _invoice(db, number="24312000000000000081", seller_name="西安启智合创科技有限公司",
                         buyer_name="客户甲有限公司", total_amount=Decimal("500.00"))
    sales_inv.invoice_direction = "output"
    db.commit()

    # 收款回单（付方=客户甲）→ 应与销项票配对
    incoming = BankReceipt(file_url="in.pdf", file_type="PDF", counterparty_name="客户甲有限公司",
                           amount=Decimal("500.00"), trade_date=_date(2026, 7, 5), status="unmatched",
                           direction="收")
    db.add(incoming)
    db.commit()
    assert suggest_pair(db, incoming.id) == sales_inv.id


# ---- P1-3 补贴计算（差旅伙食补助：天数 × 日标准 → 内部凭证） -----------------


def _allowance_entry(db, user, **scene):
    """差旅-伙食补助事项（P1-3 的主角）。"""
    claim = svc.create_claim(db, user, title="差旅报销")
    entry = svc.create_entry(
        db, user, claim.id, "travel", "伙食补助",
        scene_fields={"subtype": "allowance", **scene},
    )
    return claim, entry


def test_allowance_auto_creates_internal_voucher(db, users):
    """补助没有发票（国税函〔2009〕3 号：差旅费津贴凭内部凭证扣除），
    故按 天数 × 日标准 自动物化成一条 internal 凭证 —— 事项金额才可见。"""
    claim, entry = _allowance_entry(db, users["emp"], days="4", daily_standard="100")

    item = db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).one()
    assert item.voucher_type == "internal"
    assert item.amount == Decimal("400.00")
    assert item.auto_rule == "travel_allowance"
    assert item.expense_type == "travel"
    assert item.deductible is True
    db.refresh(entry)
    db.refresh(claim)
    assert entry.amount == Decimal("400.00")
    assert claim.total_amount == Decimal("400.00")


def test_allowance_defaults_to_company_standard(db, users):
    """日标准留空 → 用公司标准（settings），避免「只填天数却算不出钱」。"""
    _, entry = _allowance_entry(db, users["emp"], days="2")
    item = db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).one()
    assert item.amount == Decimal("200.00")  # 公司标准 100 元/天


def test_allowance_update_is_idempotent(db, users):
    """改天数/标准 → 只更新同一条自动凭证（幂等），不新增、不重复累加。"""
    claim, entry = _allowance_entry(db, users["emp"], days="4", daily_standard="100")
    svc.update_entry(
        db, users["emp"], entry.id,
        scene_fields={"subtype": "allowance", "days": "6", "daily_standard": "80"},
    )

    items = db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).all()
    assert len(items) == 1
    assert items[0].amount == Decimal("480.00")
    db.refresh(claim)
    assert claim.total_amount == Decimal("480.00")  # 不累加成 880


def test_allowance_rejects_bad_numbers(db, users):
    with pytest.raises(ValueError, match="补助天数"):
        _allowance_entry(db, users["emp"], days="0", daily_standard="100")
    with pytest.raises(ValueError, match="补助天数"):
        _allowance_entry(db, users["emp"], days="两天", daily_standard="100")
    with pytest.raises(ValueError, match="日补助标准"):
        _allowance_entry(db, users["emp"], days="2", daily_standard="-1")


def test_allowance_entry_is_submittable(db, users):
    """补助事项（无发票）不再被「还没有关联凭证」拦住 —— P1-3 的验收点。"""
    claim, entry = _allowance_entry(db, users["emp"], days="3", daily_standard="100")
    assert entry.amount == Decimal("300.00")
    svc.submit_claim(db, users["emp"], claim.id)
    db.refresh(claim)
    assert claim.status == ExpenseClaimStatus.PENDING
    assert claim.total_amount == Decimal("300.00")


def test_auto_item_not_removable_and_removed_with_entry(db, users):
    """自动凭证不可单独删除（否则金额与天数脱钩）；删事项时随事项一并清理。"""
    claim, entry = _allowance_entry(db, users["emp"], days="3", daily_standard="100")
    item = db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).one()
    with pytest.raises(ValueError, match="自动计算"):
        svc.remove_item(db, users["emp"], item.id)

    svc.remove_entry(db, users["emp"], entry.id)
    assert db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).count() == 0


def test_allowance_subtype_switch_withdraws_auto_item(db, users):
    """事项改成非补助子类 → 自动凭证撤销（否则留下一笔算不清的钱）。"""
    claim, entry = _allowance_entry(db, users["emp"], days="3", daily_standard="100")
    svc.update_entry(
        db, users["emp"], entry.id,
        scene_fields={"subtype": "local_transport", "city": "北京", "travel_date": "2026-06-11"},
    )
    assert db.query(ExpenseItem).filter(ExpenseItem.entry_id == entry.id).count() == 0
    db.refresh(claim)
    assert claim.total_amount == Decimal("0.00")


def test_add_receipt_suggests_voucher_type_by_category(db, users):
    """报销加回单：税费类自动用「缴款书回单」凭证类型；采购类用「银行回单」。"""
    tax_r = BankReceipt(file_url="tax.pdf", file_type="PDF", counterparty_name="国家金库陕西省西咸新区支库",
                        amount=Decimal("1116.00"), trade_date=date(2026, 5, 12), status="unmatched",
                        direction="付", category="tax")
    buy_r = BankReceipt(file_url="buy.pdf", file_type="PDF", counterparty_name="供应商甲",
                        amount=Decimal("800.00"), trade_date=date(2026, 5, 13), status="unmatched",
                        direction="付", category="purchase")
    db.add_all([tax_r, buy_r])
    db.commit()
    claim, entry = _claim_with_entry(db, users["emp"], title="税费与采购", entry_type="office",
                                     entry_title="税费缴款")

    tax_item = svc.add_receipt(db, users["emp"], claim.id, tax_r.id, entry.id,
                               expense_type="office", note="税费缴款")
    assert tax_item.voucher_type == "tax_receipt"

    buy_item = svc.add_receipt(db, users["emp"], claim.id, buy_r.id, entry.id,
                               expense_type="office", note="采购")
    assert buy_item.voucher_type == "bank_receipt"
