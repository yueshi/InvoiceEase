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


# ---- 审计主体：能回答"谁干的" -----------------------------------------------


def test_write_audit_records_subject(db, users, mcp_auth):
    """MCP 写操作的审计要带上**主体**：user_id + 令牌 + 租户。

    此前只记 channel="mcp"，能回答「有 Agent 干过这件事」，
    回答不了「谁干的」——等保要求的「主体」要素在 MCP 通道是空的。
    """
    from invoicing.models import AuditLog
    from invoicing.mcp import tools as mt

    # 收票为管理员专属（2026-10-05 角色门），审计主体验证改用管理员身份
    mcp_auth(users["admin"], scopes=("invoice:read", "invoice:write"), token_id=77)
    mt.fetch_invoices(mailbox_id=None)  # 无邮箱 → 空转，但审计照写

    log = (
        db.query(AuditLog)
        .filter(AuditLog.action == "FETCH", AuditLog.channel == "mcp")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert log is not None
    assert log.user_id == users["admin"].id  # 主体落到人
    assert log.detail["token_id"] == 77  # 以及"用哪个令牌"
    assert log.detail["token_source"] == "token"
    assert log.detail["tenant_id"] == "default"


def test_audit_records_legacy_source(db, users, mcp_auth):
    """legacy 令牌的审计同样可辨识来源（token_id 为空但 source 明确）。"""
    from invoicing.models import AuditLog
    from invoicing.mcp import tools as mt

    mcp_auth(users["admin"], source="legacy", token_id=None)
    mt.fetch_invoices(mailbox_id=None)

    log = (
        db.query(AuditLog)
        .filter(AuditLog.action == "FETCH", AuditLog.channel == "mcp")
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert log.user_id == users["admin"].id
    assert log.detail["token_source"] == "legacy"
    assert log.detail["token_id"] is None


# ---- 角色门：REST 财务/管理员专属的操作，Agent/令牌同口径（2026-10-05 修订）----


def test_employee_cannot_call_finance_only_tools(db, users, mcp_auth):
    """员工身份调财务专属工具 → 角色门拒绝（与 REST 的 require_role 口径一致）。

    背景：员工 scope 预设曾含 receipt:read / invoice:write，而回单无按人收敛
    （全公司数据）、invoice_update 直取任意 id——员工经 Web 助手可读全公司
    回单、改任意发票。修复 = 预设收窄 + 工具级角色门（本用例守后者）。
    """
    from invoicing.mcp import tools as mt
    from invoicing.mcp.identity import RoleDenied

    others = _invoice(db, "24312000000000000201", users["emp2"].id)  # 同事的票
    mcp_auth(users["emp"])

    with pytest.raises(RoleDenied):
        mt.receipt_list(month="2026-06")
    with pytest.raises(RoleDenied):
        mt.receipt_upload_status(1)
    with pytest.raises(RoleDenied):
        mt.invoice_update(others.id, review_note="篡改")
    with pytest.raises(RoleDenied):
        mt.invoice_classify(others.id, expense_type="other")
    with pytest.raises(RoleDenied):
        mt.invoice_ai_review(others.id)
    with pytest.raises(RoleDenied):
        mt.fetch_invoices()


def test_finance_can_call_finance_tools(db, users, mcp_auth):
    """财务身份不被误伤：回单可读、发票可改；管理员可触发收票。"""
    from invoicing.mcp import tools as mt

    own = _invoice(db, "24312000000000000202", users["fin"].id)

    mcp_auth(users["fin"])
    assert mt.receipt_list(month="2026-06").items == []
    assert mt.invoice_classify(own.id, expense_type="travel").id == own.id

    mcp_auth(users["admin"])
    assert mt.fetch_invoices().received == 0  # 无邮箱配置：可执行即算通过


def test_agent_tool_list_filtered_by_permissions(db, users):
    """Web 助手的工具清单按权限过滤（调用前检查）：清单 = 用户真能用的集合。

    目标（2026-10-05）：员工问「看回单」时助手不该先调用再吃 RoleDenied，
    而是清单里就没有 receipt_list，直接告知无权限。
    """
    import asyncio

    from invoicing.agent.tools_bridge import build_tools_for_user

    emp_tools = {t.name for t in asyncio.run(build_tools_for_user(users["emp"]))}
    fin_tools = {t.name for t in asyncio.run(build_tools_for_user(users["fin"]))}
    adm_tools = {t.name for t in asyncio.run(build_tools_for_user(users["admin"]))}

    # 员工：财务专属（角色门）与 report/sales/masterdata/admin 类全部不可见
    assert not {"receipt_list", "invoice_update", "invoice_fetch",
                "invoice_stats", "invoice_delete"} & emp_tools
    # 合法能力保留（交票/查自己的票/报销）
    assert {"invoice_list", "invoice_detail", "expense_create", "extract_invoice"} <= emp_tools

    # 财务：财务专属可见；管理员专属（收票、删票）仍不可见
    assert {"receipt_list", "invoice_update", "invoice_stats"} <= fin_tools
    assert not {"invoice_fetch", "invoice_delete"} & fin_tools

    # 管理员：全集
    assert len(adm_tools) == 39


# ---- 覆盖性：39 个工具一个都不能漏 ------------------------------------------


def test_every_registered_tool_declares_scope():
    """**防漏网**：所有注册的 MCP 工具都必须声明 scope。

    漏一个 = 那个工具对任何令牌开放（只有服务器级门槛，而它默认是空的）。
    这比逐个核对更可靠——将来加工具忘了声明会立刻红。
    """
    import asyncio

    from invoicing.mcp.server import build_server

    server = build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert len(names) == 39, f"工具数变化（{len(names)}），请同步更新设计附录 A"

    from invoicing.mcp import extract as mt_extract
    from invoicing.mcp import tools as mt

    # server.py 注册名 → 实现函数名
    impl_map = {
        "invoice_fetch": mt.fetch_invoices,
        "invoice_list": mt.list_invoices_mcp,
        "invoice_detail": mt.get_invoice_mcp,
        "extract_invoice": mt_extract.extract_invoice_file,
        "batch_extract_invoices": mt_extract.batch_extract_invoice_files,
        "validate_invoice": mt_extract.validate_invoice_data,
        "invoice_ingest": mt.ingest_invoice,
        "company_info_list": mt.company_info_list,
        "company_info_save": mt.company_info_save,
        "expense_create": mt.expense_create,
        "expense_add_entry": mt.expense_add_entry,
        "expense_add_invoices": mt.expense_add_invoices,
        "expense_add_receipt": mt.expense_add_receipt,
        "expense_add_voucher": mt.expense_add_voucher,
        "expense_submit": mt.expense_submit,
        "expense_list": mt.expense_list,
        "expense_approve": mt.expense_approve,
        "expense_eligible_invoices": mt.expense_eligible_invoices,
        "bank_account_list": mt.bank_account_list,
        "bank_account_save": mt.bank_account_save,
        "bank_account_delete": mt.bank_account_delete,
        "company_info_delete": mt.company_info_delete,
        "invoice_update": mt.invoice_update,
        "invoice_delete": mt.invoice_delete,
        "invoice_unblock": mt.invoice_unblock,
        "invoice_classify": mt.invoice_classify,
        "invoice_ai_review": mt.invoice_ai_review,
        "invoice_report": mt.invoice_report,
        "invoice_stats": mt.invoice_stats,
        "sales_invoice_import": mt.sales_invoice_import,
        "sales_invoice_import_list": mt.sales_invoice_import_list,
        "red_invoice_list": mt.red_invoice_list,
        "red_invoice_link": mt.red_invoice_link,
        "receipt_ingest": mt.receipt_ingest,
        "receipt_parse_status": mt.receipt_upload_status,
        "receipt_list": mt.receipt_list,
        "receipt_pair": mt.receipt_pair,
        "receipt_report": mt.receipt_report,
        "invoice_health_report": mt.invoice_health_report,
    }
    assert set(impl_map) == names, (
        f"注册名与实现映射不一致：注册多出 {names - set(impl_map)}，映射多出 {set(impl_map) - names}"
    )

    # requires 用 functools.wraps，被包裹的函数带 __wrapped__
    unprotected = [reg for reg, fn in impl_map.items() if not hasattr(fn, "__wrapped__")]
    assert unprotected == [], f"以下工具未声明 scope：{unprotected}"

    # 权限过滤解析器（Web 助手调用前过滤的数据源）必须与注册一一对应——
    # 别名表漂移会让过滤放行/误藏错误的对象，这里逐名核对到函数本体
    for reg, fn in impl_map.items():
        assert mt._impl_of(reg) is fn, f"{reg} 的过滤解析指向了错误的实现函数"
