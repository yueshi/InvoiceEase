"""MCP 身份与权限：Principal 抽象、工具层取身份、scope 校验。

设计见 design/2026-09-13-MCP身份与权限设计.md §4.2/§4.5。
本文件不依赖真实令牌表——直接往 SDK 的 auth_context_var 里放 AuthenticatedUser
（这正是 AuthContextMiddleware 在生产做的事，已由 spike 实测确认可穿透到 sync 工具）。
"""
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from invoicing.mcp import identity as ident


@pytest.fixture()
def auth_ctx():
    """把认证上下文注入/清理（替代生产链路的 AuthContextMiddleware）。"""
    tokens = []

    def _set(*, user_id=7, scopes=("invoice:read",), role="employee",
             username="zhangsan", tenant_id="default", source="token", token_id=3):
        tok = AccessToken(
            token="opaque", client_id="workbuddy", scopes=list(scopes),
            subject=str(user_id),
            claims={"username": username, "role": role, "tenant_id": tenant_id,
                    "source": source, "token_id": token_id, "iss": "invoicing"},
        )
        tokens.append(auth_context_var.set(AuthenticatedUser(tok)))

    yield _set
    for t in reversed(tokens):
        auth_context_var.reset(t)


def test_scopes_and_role_presets_are_consistent():
    """13 个 scope 是唯一权威；角色预设只能引用它们，admin 为全集。"""
    assert set(ident.ROLE_DEFAULT_SCOPES) == {"employee", "finance_staff", "finance_manager", "admin"}
    for role, scopes in ident.ROLE_DEFAULT_SCOPES.items():
        assert set(scopes) <= set(ident.SCOPES), f"{role} 引用了未定义的 scope"
    assert set(ident.ROLE_DEFAULT_SCOPES["admin"]) == set(ident.SCOPES)
    # 员工不应拿到审批与删除类权限
    emp = set(ident.ROLE_DEFAULT_SCOPES["employee"])
    assert not emp & {"expense:approve", "invoice:admin", "masterdata:write"}


def test_current_principal_requires_auth_context():
    """无认证上下文 → 抛错。**不得**静默降级为 admin——那正是要修的缺陷。"""
    with pytest.raises(ident.NoPrincipalError):
        ident.current_principal()


def test_current_principal_reads_identity_from_context(auth_ctx):
    auth_ctx(user_id=42, scopes=("invoice:read", "expense:write"), role="finance_staff",
             username="caiwu", tenant_id="acme", source="token", token_id=9)
    p = ident.current_principal()

    assert p.user_id == 42
    assert p.username == "caiwu"
    assert p.role == "finance_staff"
    assert p.tenant_id == "acme"
    assert p.token_id == 9
    assert p.source == "token"
    assert p.scopes == frozenset({"invoice:read", "expense:write"})


def test_current_principal_supports_legacy_token(auth_ctx):
    """legacy 内建令牌：user_id 与角色来自配置映射，token_id 为空但审计仍可辨识。"""
    auth_ctx(user_id=1, scopes=tuple(ident.SCOPES), role="admin",
             username="admin", source="legacy", token_id=None)
    p = ident.current_principal()

    assert p.source == "legacy" and p.token_id is None
    assert p.role == "admin" and p.has("invoice:admin")


def test_principal_has_scope_semantics():
    p = ident.Principal(user_id=1, username="u", role="employee", tenant_id="default",
                        scopes=frozenset({"invoice:read", "expense:write"}),
                        source="token", token_id=1)
    assert p.has("invoice:read")
    assert p.has("invoice:read", "expense:write")  # 全含才算有
    assert not p.has("invoice:read", "invoice:admin")
    assert not p.has("invoice:admin")
    assert p.has()  # 空要求恒真


def test_requires_allows_when_scope_present(auth_ctx):
    auth_ctx(scopes=("invoice:read", "invoice:write"))

    @ident.requires("invoice:write")
    def do_write() -> str:
        return "ok"

    assert do_write() == "ok"


def test_requires_denies_and_names_missing_scopes(auth_ctx):
    auth_ctx(scopes=("invoice:read",))

    @ident.requires("invoice:admin")
    def do_delete() -> str:  # pragma: no cover - 不应执行
        raise AssertionError("越权函数体不应被调用")

    with pytest.raises(ident.ScopeDenied) as ei:
        do_delete()
    msg = str(ei.value)
    assert "invoice:admin" in msg  # 文案要指出缺哪个 scope，便于 Agent 自我纠正
    assert "当前令牌" in msg


def test_requires_denies_without_context():
    @ident.requires("invoice:read")
    def do_read() -> str:  # pragma: no cover
        raise AssertionError("不应执行")

    with pytest.raises(ident.NoPrincipalError):
        do_read()


def test_requires_preserves_function_metadata(auth_ctx):
    auth_ctx(scopes=("invoice:read",))

    @ident.requires("invoice:read")
    def invoice_list(page: int = 1) -> dict:
        """工具描述必须保留——MCP 从 docstring/签名生成工具 schema。"""
        return {"page": page}

    assert invoice_list.__name__ == "invoice_list"
    assert "工具描述必须保留" in (invoice_list.__doc__ or "")
    assert invoice_list(page=2) == {"page": 2}
