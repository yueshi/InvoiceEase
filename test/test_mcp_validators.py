"""3 个业务校验 MCP 工具测试（P1：行程/餐补/补录归属）。

工具为**只读**：调用不得产生业务副作用、不得写审计（v1.1 §7.1 ❌1）。
"""
from datetime import date
from decimal import Decimal

import pytest

from invoicing.mcp import tools as mt
from invoicing.models import Invoice, Role, User
from invoicing.models.audit import AuditLog
from invoicing.workflow import expenses as svc
from invoicing.workflow.policy_seed import seed_default_policies


@pytest.fixture(autouse=True)
def _ctx(mcp_admin_auth):
    """工具体带 @requires，直调需要认证上下文。"""


def _claim_with_travel(db, user, *, add_return=True, nights=None):
    claim = svc.create_claim(db, user, title="上海出差")
    leg = svc.create_entry(
        db, user, claim.id, "travel", "去程",
        scene_fields={"subtype": "transport", "transport_mode": "高铁",
                      "from_city": "上海", "to_city": "北京",
                      "travel_date": "2026-06-10"})
    if add_return:
        svc.create_entry(
            db, user, claim.id, "travel", "返程",
            scene_fields={"subtype": "transport", "transport_mode": "高铁",
                          "from_city": "北京", "to_city": "上海",
                          "travel_date": "2026-06-12"})
    if nights is not None:
        svc.create_entry(
            db, user, claim.id, "travel", "住宿",
            scene_fields={"subtype": "accommodation", "city": "北京",
                          "checkin": "2026-06-10", "checkout": "2026-06-12",
                          "nights": str(nights)})
    return claim, leg


# ---- validate_trip_consistency --------------------------------------------

def test_validate_trip_consistency_clean(db, mcp_admin_auth):
    claim, _ = _claim_with_travel(db, mcp_admin_auth)
    out = mt.validate_trip_consistency(claim.id)
    assert out["ok"] is True and out["outcome"] == "PASS"
    assert out["issues"] == []


def test_validate_trip_consistency_flags_missing_return(db, mcp_admin_auth):
    claim, _ = _claim_with_travel(db, mcp_admin_auth, add_return=False)
    out = mt.validate_trip_consistency(claim.id)
    # 返程缺失是「转人工」而非阻断（spec §4.2）——ok 保持 True，靠 outcome/severity 表达
    assert out["ok"] is True
    assert out["outcome"] == "NEEDS_REVIEW"
    assert any(i["code"] == "NO_RETURN_TRIP" and i["severity"] == "warning"
               for i in out["issues"])


def test_validate_trip_consistency_flags_nights_mismatch(db, mcp_admin_auth):
    claim, _ = _claim_with_travel(db, mcp_admin_auth, nights=5)
    out = mt.validate_trip_consistency(claim.id)
    assert any(i["code"] == "NIGHTS_MISMATCH" and i["severity"] == "error"
               for i in out["issues"])


def test_validate_trip_consistency_missing_claim_raises(db, mcp_admin_auth):
    with pytest.raises(ValueError, match="不存在"):
        mt.validate_trip_consistency(999999)


# ---- validate_meal_compliance ---------------------------------------------

def _claim_with_allowance(db, user, daily="100", amount=None):
    claim = svc.create_claim(db, user, title="补助")
    svc.create_entry(
        db, user, claim.id, "travel", "伙食补助",
        scene_fields={"subtype": "allowance", "days": "1", "daily_standard": daily})
    return claim


def test_validate_meal_compliance_within_standard(db, mcp_admin_auth):
    seed_default_policies(db, tenant_id="default")
    claim = _claim_with_allowance(db, mcp_admin_auth, daily="100")
    out = mt.validate_meal_compliance(claim.id)
    assert out["ok"] is True


def test_validate_meal_compliance_flags_over_standard(db, mcp_admin_auth):
    seed_default_policies(db, tenant_id="default")
    claim = _claim_with_allowance(db, mcp_admin_auth, daily="500")
    out = mt.validate_meal_compliance(claim.id)
    assert out["ok"] is False and out["outcome"] == "FAIL"
    assert any(i["code"] == "OVER_STANDARD" and i["severity"] == "error"
               for i in out["issues"])


def test_validate_meal_compliance_without_policy_warns(db, mcp_admin_auth):
    """未播种政策 → 降级 warning，不阻断（不把"没配标准"当违规）。"""
    claim = _claim_with_allowance(db, mcp_admin_auth, daily="100")
    out = mt.validate_meal_compliance(claim.id)
    assert out["ok"] is True  # warning 不算不通过
    assert out["outcome"] == "NEEDS_REVIEW"
    assert any(i["code"] == "NO_POLICY_CONFIGURED" for i in out["issues"])


# ---- suggest_claim_for_invoice --------------------------------------------

def _draft_claim(db, user, ctype="travel"):
    claim = svc.create_claim(db, user, title="待补录", claim_type=ctype)
    svc.create_entry(db, user, claim.id, "travel", "打车",
                     occurred_on=date(2026, 6, 11),
                     scene_fields={"subtype": "local_transport", "city": "北京",
                                   "travel_date": "2026-06-11"})
    return claim


def test_suggest_claim_for_invoice_ranks_matching_draft(db, mcp_admin_auth):
    claim = _draft_claim(db, mcp_admin_auth, ctype="travel")
    inv = Invoice(file_url="p1.xml", file_type="XML", invoice_number="INV-P1-1",
                  status="pending_submit", total_amount=Decimal("50"),
                  amount_without_tax=Decimal("50"), tax_amount=Decimal("0"),
                  issue_date=date(2026, 6, 11), expense_type="travel")
    db.add(inv)
    db.commit()
    out = mt.suggest_claim_for_invoice(inv.id)
    assert out["candidates"]
    assert out["candidates"][0]["claim_id"] == claim.id


def test_suggest_claim_for_invoice_ignores_non_draft(db, mcp_admin_auth):
    """已提交的单不再作为补录候选。"""
    claim = _draft_claim(db, mcp_admin_auth)
    # 直接置为已提交态（本用例只测候选过滤，不经提交流程）
    from invoicing.models.expense import ExpenseClaimStatus
    claim.status = ExpenseClaimStatus.PENDING.value
    db.commit()
    inv = Invoice(file_url="p1b.xml", file_type="XML", invoice_number="INV-P1-2",
                  status="pending_submit", total_amount=Decimal("50"),
                  amount_without_tax=Decimal("50"), tax_amount=Decimal("0"),
                  issue_date=date(2026, 6, 11), expense_type="travel")
    db.add(inv)
    db.commit()
    assert mt.suggest_claim_for_invoice(inv.id)["candidates"] == []


# ---- 只读性 -----------------------------------------------------------------

def test_validators_are_read_only(db, mcp_admin_auth):
    """调用校验工具不得写审计、不得产生提案（v1.1 §7.1 ❌1 AI 只读）。"""
    from invoicing.models.proposal import Proposal

    seed_default_policies(db, tenant_id="default")
    claim, _ = _claim_with_travel(db, mcp_admin_auth, nights=5)
    before_audit = db.query(AuditLog).count()

    mt.validate_trip_consistency(claim.id)
    mt.validate_meal_compliance(claim.id)

    assert db.query(AuditLog).count() == before_audit
    assert db.query(Proposal).count() == 0