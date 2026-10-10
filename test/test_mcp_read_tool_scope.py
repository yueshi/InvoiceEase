"""读工具的数据范围收敛（final review 发现：新读工具绕过 scope）。

既有约定（design/2026-09-13-MCP身份与权限设计.md）：**所有按单据出数的读路径
必须收敛**——员工只看得到自己的报销单（`expense_list` 即如此）。P1 新增的
校验工具与 P0-1 的 validate_expense 直取 claim_id，会把同事的单据明细
（金额/城市/日期/超标结论）泄漏给任何持 expense:read 的员工。
"""
import pytest

from invoicing.mcp import tools as mt
from invoicing.models import Role, User
from invoicing.workflow import expenses as svc


@pytest.fixture()
def users(db):
    emp = User(username="scope_emp", password_hash="x", role=Role.employee.value)
    other = User(username="scope_other", password_hash="x", role=Role.employee.value)
    fin = User(username="scope_fin", password_hash="x", role=Role.finance_staff.value)
    db.add_all([emp, other, fin])
    db.commit()
    return {"emp": emp, "other": other, "fin": fin}


def _others_claim(db, other):
    claim = svc.create_claim(db, other, title="同事的出差", claim_type="travel")
    svc.create_entry(db, other, claim.id, "travel", "去程",
                     scene_fields={"subtype": "transport", "transport_mode": "高铁",
                                   "from_city": "上海", "to_city": "北京",
                                   "travel_date": "2026-06-10"})
    return claim


def test_validate_trip_denies_other_users_claim(db, users, mcp_auth):
    claim = _others_claim(db, users["other"])
    mcp_auth(users["emp"])
    with pytest.raises(ValueError, match="不存在或无权访问"):
        mt.validate_trip_consistency(claim.id)


def test_validate_meal_denies_other_users_claim(db, users, mcp_auth):
    claim = _others_claim(db, users["other"])
    mcp_auth(users["emp"])
    with pytest.raises(ValueError, match="不存在或无权访问"):
        mt.validate_meal_compliance(claim.id)


def test_validate_expense_denies_other_users_claim(db, users, mcp_auth):
    """P0-1 的 validate_expense 同形，一并用同口径收敛。"""
    claim = _others_claim(db, users["other"])
    mcp_auth(users["emp"])
    with pytest.raises(ValueError, match="不存在或无权访问"):
        mt.validate_expense_mcp(db, claim.id)


def test_validators_allow_own_claim(db, users, mcp_auth):
    claim = svc.create_claim(db, users["emp"], title="我的单", claim_type="travel")
    mcp_auth(users["emp"])
    assert mt.validate_trip_consistency(claim.id)["outcome"] == "PASS"
    assert mt.validate_meal_compliance(claim.id)["outcome"] == "PASS"


def test_validators_allow_finance_on_any_claim(db, users, mcp_auth):
    claim = _others_claim(db, users["other"])
    mcp_auth(users["fin"])
    mt.validate_trip_consistency(claim.id)  # 财务全公司可见，不抛


def test_suggest_claim_denies_other_users_drafts(db, users, mcp_auth):
    """补录建议不得把同事的草稿单号/行程区间暴露给员工。"""
    from datetime import date
    from decimal import Decimal

    from invoicing.models import Invoice

    claim = _others_claim(db, users["other"])
    inv = Invoice(file_url="s.xml", file_type="XML", invoice_number="INV-SCOPE-1",
                  status="pending_submit", total_amount=Decimal("50"),
                  amount_without_tax=Decimal("50"), tax_amount=Decimal("0"),
                  issue_date=date(2026, 6, 10), expense_type="travel",
                  user_id=users["emp"].id)
    db.add(inv)
    db.commit()

    mcp_auth(users["emp"])
    out = mt.suggest_claim_for_invoice(inv.id)
    assert all(c["claim_id"] != claim.id for c in out["candidates"])


def test_suggest_claim_denies_other_users_invoice(db, users, mcp_auth):
    """他人上传的发票也不能借 suggestion 探测。"""
    from datetime import date
    from decimal import Decimal

    from invoicing.models import Invoice

    inv = Invoice(file_url="s2.xml", file_type="XML", invoice_number="INV-SCOPE-2",
                  status="pending_submit", total_amount=Decimal("50"),
                  amount_without_tax=Decimal("50"), tax_amount=Decimal("0"),
                  issue_date=date(2026, 6, 10), expense_type="travel",
                  user_id=users["other"].id)
    db.add(inv)
    db.commit()

    mcp_auth(users["emp"])
    with pytest.raises(ValueError, match="不存在或无权访问"):
        mt.suggest_claim_for_invoice(inv.id)