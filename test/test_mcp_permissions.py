"""MCP 权限与数据范围：FRD §380 验收「普通员工仅能看到本人发票，财务可看全公司」。

设计见 design/2026-09-13-MCP身份与权限设计.md §8（验收标准）。

与 test_mcp_identity.py 的分工：那边测 Principal/requires 本身，
这里测**工具在真实身份下拿到的数据范围**——即改造是否真的生效。
"""
import pytest

from invoicing.models import Invoice, McpToken, Role, User


@pytest.fixture()
def users(db):
    emp = User(username="emp", password_hash="x", role=Role.employee.value)
    emp2 = User(username="emp2", password_hash="x", role=Role.employee.value)
    fin = User(username="fin", password_hash="x", role=Role.finance_staff.value)
    admin = User(username="adm", password_hash="x", role=Role.admin.value)
    db.add_all([emp, emp2, fin, admin])
    db.commit()
    return {"emp": emp, "emp2": emp2, "fin": fin, "admin": admin}


def _invoice(db, number, user_id, amount="100.00"):
    inv = Invoice(
        file_url=f"{number}.xml", file_type="XML", invoice_number=number,
        status="pending_submit", verify_status="passed", total_amount=amount,
        user_id=user_id, seller_name="某某公司",
    )
    db.add(inv)
    db.commit()
    return inv


# ---- FRD §380：数据范围隔离 -------------------------------------------------


def test_employee_sees_only_own_invoices(db, users, mcp_auth):
    """**FRD §380 验收**：员工令牌调 invoice_list 只返回本人上传的发票。"""
    from invoicing.mcp import tools as mt

    mine = _invoice(db, "24312000000000000101", users["emp"].id)
    others = _invoice(db, "24312000000000000102", users["emp2"].id)
    public = _invoice(db, "24312000000000000103", None)  # 公共池（邮箱归集，无归属人）

    mcp_auth(users["emp"])
    ids = {i.id for i in mt.list_invoices_mcp(page=1, page_size=50).items}
    assert mine.id in ids
    assert others.id not in ids  # 关键：看不到同事的
    assert public.id not in ids  # 公共池也不给普通员工（scope 口径见设计 §4.2）


def test_finance_sees_whole_company(db, users, mcp_auth):
    """财务看全公司（FRD §380 的另一半）。"""
    from invoicing.mcp import tools as mt

    a = _invoice(db, "24312000000000000104", users["emp"].id)
    b = _invoice(db, "24312000000000000105", users["emp2"].id)

    mcp_auth(users["fin"])
    ids = {i.id for i in mt.list_invoices_mcp(page=1, page_size=50).items}
    assert {a.id, b.id} <= ids


def test_employee_cannot_read_others_invoice_by_id(db, users, mcp_auth):
    """按 ID 直查他人发票 → 拒绝（否则列表过滤形同虚设）。"""
    from invoicing.mcp import tools as mt

    others = _invoice(db, "24312000000000000106", users["emp2"].id)

    mcp_auth(users["emp"])
    with pytest.raises(ValueError, match="不存在或无权访问"):
        mt.get_invoice_mcp(others.id)


def test_employee_sees_only_own_claims(db, users, mcp_auth):
    from invoicing.mcp import tools as mt
    from invoicing.workflow import expenses as svc

    svc.create_claim(db, users["emp"], title="我的单")
    svc.create_claim(db, users["emp2"], title="同事的单")

    mcp_auth(users["emp"])
    assert {c["title"] for c in mt.expense_list()} == {"我的单"}


# ---- scope 校验：能力上限 ---------------------------------------------------


def test_missing_scope_denied(db, users, mcp_auth):
    """缺 expense:approve 的令牌调审批 → 被拒（角色够也不行：scope 是能力上限）。"""
    from invoicing.mcp import tools as mt
    from invoicing.workflow import expenses as svc

    claim = svc.create_claim(db, users["fin"], title="待审批")

    # 财务角色（role 允许审批）但令牌没给该 scope
    mcp_auth(users["fin"], scopes=("expense:read", "expense:write"))
    with pytest.raises(ValueError, match="expense:approve"):
        mt.expense_approve(claim.id, action="approve")


def test_scope_and_role_both_must_pass(db, users, mcp_auth):
    """**取交集**：scope 给了但角色不够（员工持 approve scope）→ 仍被拒。

    直接把单置为待审批（本用例测的是角色闸门，不是提交流程；
    approve_claim 先查状态后查角色，草稿态会先撞状态检查）。
    """
    from invoicing.mcp import tools as mt
    from invoicing.models import ExpenseClaimStatus
    from invoicing.workflow import expenses as svc

    claim = svc.create_claim(db, users["emp"], title="自己的单")
    claim.status = ExpenseClaimStatus.PENDING
    db.commit()

    mcp_auth(users["emp"], scopes=("expense:read", "expense:approve"))
    with pytest.raises(ValueError, match="财务角色"):
        mt.expense_approve(claim.id, action="approve")


def test_no_auth_context_denied(db, users):
    """无认证上下文直接调工具 → 抛错，**不得**退回管理员身份。"""
    from invoicing.mcp import tools as mt
    from invoicing.mcp.identity import NoPrincipalError

    with pytest.raises(NoPrincipalError):
        mt.list_invoices_mcp(page=1, page_size=10)


# ---- legacy 令牌：零中断等价性 ---------------------------------------------


def test_legacy_token_behaves_like_before(db, users, mcp_auth):
    """legacy 内建令牌 = 管理员通道（行为与升级前等价），审计可辨识来源。"""
    from invoicing.mcp import tools as mt

    a = _invoice(db, "24312000000000000107", users["emp"].id)
    b = _invoice(db, "24312000000000000108", users["emp2"].id)

    mcp_auth(users["admin"], source="legacy", token_id=None)
    ids = {i.id for i in mt.list_invoices_mcp(page=1, page_size=50).items}
    assert {a.id, b.id} <= ids  # 与升级前一致：看全公司


def test_token_bound_user_missing_rejected(db, users, mcp_auth):
    """令牌绑定的用户被删 → 明确报错，不静默降级。"""
    from invoicing.mcp import tools as mt

    ghost = User(username="ghost", password_hash="x", role=Role.employee.value)
    db.add(ghost)
    db.commit()
    ghost_id = ghost.id
    db.delete(ghost)
    db.commit()

    mcp_auth(users["emp"])
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    tok = AccessToken(token="t", client_id="c", scopes=["invoice:read"], subject=str(ghost_id),
                      claims={"username": "ghost", "role": "employee", "tenant_id": "default",
                              "source": "token", "token_id": 1, "iss": "test"})
    auth_context_var.set(AuthenticatedUser(tok))
    with pytest.raises(ValueError, match="归属用户不存在"):
        mt.list_invoices_mcp(page=1, page_size=10)
