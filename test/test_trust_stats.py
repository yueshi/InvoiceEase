"""信任仪表盘统计测试（M8：改判率数据源=B1 埋点）。"""
from invoicing.models import AuditLog, Invoice


def _log(db, action, detail, invoice_id=1):
    log = AuditLog(action=action, invoice_id=invoice_id, detail=detail, channel="web")
    db.add(log)
    db.flush()


def test_trust_stats_counts_auto_and_overturns(db):
    from invoicing.api.stats import trust_stats

    inv = Invoice(file_url="a", file_type="XML", invoice_number="24312000000012345678")
    db.add(inv)
    db.flush()
    _log(db, "AUTO_REVIEW", {"note": "数字员工自动通过", "reason": "x", "confidence": 0.96})
    _log(db, "REVIEW", {"action": "reject", "ai_verdict": "approve", "ai_confidence": 0.9})  # 改判
    _log(db, "REVIEW", {"action": "approve", "ai_verdict": "approve", "ai_confidence": 0.9})  # 一致
    _log(db, "REVIEW", {"action": "approve", "ai_verdict": "uncertain", "ai_confidence": 0.6})  # 无明确方向不计改判
    stats = trust_stats(db, 7)
    assert stats["auto_count"] == 1
    assert stats["manual_count"] == 3
    assert stats["overturn_count"] == 1
    assert stats["overturn_rate"] == 0.5  # 1 改判 / 2 有明确预判的人工复核


def test_trust_stats_excludes_review_without_ai_verdict(db):
    """无预判的人工复核（ai_verdict=None）不计入改判率分母之外的统计。"""
    from invoicing.api.stats import trust_stats

    inv = Invoice(file_url="a", file_type="XML", invoice_number="24312000000012345679")
    db.add(inv)
    db.flush()
    _log(db, "REVIEW", {"action": "approve", "ai_verdict": None, "ai_confidence": None})
    stats = trust_stats(db, 7)
    assert stats["manual_count"] == 1
    assert stats["overturn_count"] == 0
    assert stats["overturn_rate"] == 0.0
