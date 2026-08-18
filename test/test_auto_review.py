"""渐进自主引擎测试（M8：阈值自动执行，拦截永不自动）。"""
from datetime import date
from decimal import Decimal

from invoicing.models import AuditLog, Invoice
from invoicing.parse.ai_review import auto_review_predictions


def _seed_pending(db, verdict, confidence):
    inv = Invoice(
        file_url="a.xml", file_type="XML", invoice_number="24312000000012345678",
        status="pending_review", total_amount=Decimal("100.00"),
        seller_name="示例科技有限公司", issue_date=date(2026, 8, 1),
        parse_source="XML", confidence_score=1.0,
        ai_review_verdict=verdict, ai_review_confidence=confidence,
        ai_review_reason="字段完整且校验通过",
    )
    db.add(inv)
    db.flush()
    return inv


def test_threshold_zero_never_auto(db):
    """观察期（threshold=0）：任何票都不自动执行。"""
    inv = _seed_pending(db, "approve", 0.99)
    assert auto_review_predictions(db, 0) == 0
    db.refresh(inv)
    assert inv.status == "pending_review"


def test_approve_above_threshold_auto_passes(db):
    """approve 且 conf ≥ 阈值 → 自动通过 + AUTO_REVIEW 审计。"""
    inv = _seed_pending(db, "approve", 0.96)
    assert auto_review_predictions(db, 0.95) == 1
    db.refresh(inv)
    assert inv.status == "pending_submit"
    logs = db.query(AuditLog).filter(AuditLog.action == "AUTO_REVIEW").all()
    assert len(logs) == 1
    assert "自动" in logs[0].detail["note"]
    assert logs[0].detail["reason"] == "字段完整且校验通过"  # 预判理由原样留痕


def test_below_threshold_stays_pending(db):
    """conf < 阈值 → 不自动。"""
    inv = _seed_pending(db, "approve", 0.9)
    assert auto_review_predictions(db, 0.95) == 0
    db.refresh(inv)
    assert inv.status == "pending_review"


def test_reject_never_auto(db):
    """拦截方向永不自动（即使 conf=1.0）。"""
    inv = _seed_pending(db, "reject", 1.0)
    assert auto_review_predictions(db, 0.95) == 0
    db.refresh(inv)
    assert inv.status == "pending_review"
