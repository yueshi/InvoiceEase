"""Proposal 模型测试（P0-2 两段握手，v1.1 §7.5）。"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from invoicing.models.proposal import Proposal


def test_proposal_creation(db):
    p = Proposal(
        token="t_" + "x" * 60,
        tool_name="expense_create",
        payload={"title": "t"},
        preview={"description": "创建报销单：t"},
        actor_id=1,
        actor_type="user",
        channel="mcp",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(p)
    db.commit()
    assert p.token.startswith("t_")
    assert p.consumed_at is None
    assert p.created_at is not None


def test_proposal_token_unique(db):
    params = dict(tool_name="x", payload={}, preview={}, actor_id=1,
                  actor_type="user", channel="mcp",
                  expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
    db.add(Proposal(token="dup", **params))
    db.commit()
    db.add(Proposal(token="dup", **params))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_proposal_payload_json_roundtrip(db):
    p = Proposal(
        token="p1",
        tool_name="expense_create",
        payload={"title": "出差", "amount": 110, "nested": {"k": "v"}},
        preview={"description": "..."},
        actor_id=1,
        actor_type="user",
        channel="mcp",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    assert p.payload["title"] == "出差"
    assert p.payload["nested"]["k"] == "v"