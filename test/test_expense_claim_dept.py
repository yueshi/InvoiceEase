"""ExpenseClaim 加 dept 字段测试（P0-1 validate_expense 配套）。

dept 是「这张报销属于哪个部门」的业务属性，与 claim_type 并列。
历史数据 backfill 为空字符串（不影响 budget check，validate 时给 NO_DEPT warning）。
"""
from decimal import Decimal
from datetime import date

from invoicing.models import Role, User
from invoicing.models.expense import ExpenseClaim, ExpenseClaimStatus, EntryType


def test_expense_claim_has_dept_field(db):
    u = User(username="u1", password_hash="x", role=Role.employee.value)
    db.add(u); db.flush()
    c = ExpenseClaim(
        tenant_id="default",
        claim_no="C2026-001",
        applicant_id=u.id,
        title="差旅",
        claim_type=EntryType.TRAVEL.value,
        total_amount=Decimal("100"),
        dept="engineering",
    )
    db.add(c); db.commit()
    assert c.dept == "engineering"


def test_expense_claim_dept_nullable_for_backfill(db):
    """历史数据可以 dept=NULL（迁移后 backfill 默认空串，但允许 NULL 兼容）。"""
    u = User(username="u1", password_hash="x", role=Role.employee.value)
    db.add(u); db.flush()
    c = ExpenseClaim(
        tenant_id="default",
        claim_no="C2026-002",
        applicant_id=u.id,
        title="差旅",
        claim_type=EntryType.TRAVEL.value,
        total_amount=Decimal("100"),
    )
    db.add(c); db.commit()
    # 字段存在，允许 NULL（迁移期兼容）
    assert c.dept is None


def test_expense_claim_dept_uses_claim_type_as_category(db):
    """claim_type 字段已存在，作为 budget category 使用。"""
    u = User(username="u1", password_hash="x", role=Role.employee.value)
    db.add(u); db.flush()
    c = ExpenseClaim(
        tenant_id="default",
        claim_no="C2026-003",
        applicant_id=u.id,
        title="差旅",
        claim_type=EntryType.TRAVEL.value,
        total_amount=Decimal("100"),
        dept="eng",
    )
    db.add(c); db.commit()
    assert c.claim_type == "travel"
    assert c.dept == "eng"