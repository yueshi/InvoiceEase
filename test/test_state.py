import pytest

from invoicing.models import Invoice, InvoiceStatus
from invoicing.workflow.state import TRANSITIONS, can_transition, transition


def test_legal_transitions():
    assert can_transition("received", "parsing")
    assert can_transition("parsing", "pending_review")
    assert can_transition("parsing", "blocked")  # 解析冲突→查重拦截
    assert can_transition("verifying", "pending_submit")
    assert can_transition("verifying", "blocked")
    assert can_transition("pending_review", "rejected")
    assert can_transition("pending_review", "pending_submit")
    assert can_transition("pending_submit", "verifying")


def test_illegal_transitions():
    assert not can_transition("received", "parsed")  # 跳级非法
    assert not can_transition("blocked", "pending_submit")  # 拦截后不可放行
    assert not can_transition("archived", "parsing")
    assert not can_transition("pending_submit", "received")


def test_transition_applies_status():
    inv = Invoice(file_url="a.xml", file_type="XML", status="received")
    transition(inv, "parsing")
    assert inv.status == "parsing"


def test_transition_raises_on_illegal():
    inv = Invoice(file_url="a.xml", file_type="XML", status="blocked")
    with pytest.raises(ValueError, match="非法状态转换"):
        transition(inv, "pending_submit")


def test_terminal_states_defined():
    for status in InvoiceStatus:
        assert status.value in TRANSITIONS  # 所有状态都有转换表条目
