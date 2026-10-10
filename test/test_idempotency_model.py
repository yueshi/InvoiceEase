"""IdempotencyKey 模型测试（P0-2 幂等键，v1.1 §7.5）。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models.idempotency import IdempotencyKey


def _expires():
    return datetime.now(timezone.utc) + timedelta(hours=24)


def test_idempotency_key_creation(db):
    k = IdempotencyKey(
        key="client-req-001", tool_name="expense_create",
        response={"ok": True, "claim_id": 42}, expires_at=_expires(),
    )
    db.add(k)
    db.commit()
    assert k.created_at is not None


def test_idempotency_unique_per_tool(db):
    db.add(IdempotencyKey(key="k1", tool_name="expense_create",
                          response={"a": 1}, expires_at=_expires()))
    db.commit()
    db.add(IdempotencyKey(key="k1", tool_name="expense_create",
                          response={"a": 2}, expires_at=_expires()))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_idempotency_same_key_different_tool_allowed(db):
    """同 key 不同 tool 不冲突（防跨工具 key 碰撞）。"""
    db.add_all([
        IdempotencyKey(key="shared", tool_name="expense_create",
                       response={"a": 1}, expires_at=_expires()),
        IdempotencyKey(key="shared", tool_name="invoice_update",
                       response={"b": 2}, expires_at=_expires()),
    ])
    db.commit()
    assert db.query(IdempotencyKey).count() == 2