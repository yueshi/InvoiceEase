"""MCP 身份与权限：Principal 抽象 + 工具层取身份 + scope 校验。

设计见 design/2026-09-13-MCP身份与权限设计.md §4.2/§4.5。

分工（**两个正交维度，取交集**）：
- `role`   这个人**能看到哪些数据** → 交给 service 层现有 RBAC（本模块不重复实现）
- `scopes` 这个令牌**能调哪些工具** → 本模块 `requires()` 校验（能力上限）

身份从 SDK 的认证上下文读取（`AuthContextMiddleware` 写入的 ContextVar，
已由 spike 实测确认可穿透到同步工具函数）。**不查库**——verifier 每次请求都会
重新组装 claims，所以角色变更立即生效。
"""
import functools
from dataclasses import dataclass
from typing import Callable

# 13 个 scope 覆盖 39 个工具（映射表见设计文档附录 A）
SCOPES: tuple[str, ...] = (
    "invoice:read", "invoice:write", "invoice:admin",
    "expense:read", "expense:write", "expense:approve",
    "receipt:read", "receipt:write",
    "sales:read", "sales:write",
    "masterdata:read", "masterdata:write",
    "report:read",
)

# 角色默认 scope 预设（UI 一键选择后可再微调）；admin 为全集
# 修订（2026-10-05）：employee 去掉 receipt:read——回单在 REST/UI 均为财务专属，
# 且回单是全公司数据、无按人收敛，员工持有该 scope 即可读到全公司回单（已复现）
ROLE_DEFAULT_SCOPES: dict[str, tuple[str, ...]] = {
    "employee": (
        "invoice:read", "invoice:write", "expense:read", "expense:write",
    ),
    "finance_staff": (
        "invoice:read", "invoice:write", "expense:read", "expense:write", "expense:approve",
        "receipt:read", "receipt:write", "sales:read", "report:read",
    ),
    "finance_manager": (
        "invoice:read", "invoice:write", "expense:read", "expense:write", "expense:approve",
        "receipt:read", "receipt:write", "sales:read", "report:read",
    ),
    "admin": SCOPES,
}


class NoPrincipalError(ValueError):
    """工具在无认证上下文时被调用。

    正常链路上不应发生——SDK 的 RequireAuthMiddleware 会在进入工具前拦下无令牌请求。
    这里**故意抛错而非降级为管理员**：静默降级正是本设计要修的缺陷。
    """


class ScopeDenied(ValueError):
    """令牌 scope 不足。继承 ValueError 以复用既有「工具抛错 → is_error」链路。"""


class RoleDenied(ValueError):
    """角色不满足工具要求（如员工调用财务专属工具）。与 ScopeDenied 同链路。"""


@dataclass(frozen=True)
class Principal:
    """一次 MCP 调用的主体（人 + 令牌）。"""

    user_id: int | None
    username: str
    role: str
    tenant_id: str
    scopes: frozenset[str]
    source: str  # "token" | "legacy"
    token_id: int | None

    def has(self, *scopes: str) -> bool:
        """是否具备全部所列 scope（空要求恒真）。"""
        return all(s in self.scopes for s in scopes)


def current_principal() -> Principal:
    """从 SDK 认证上下文取当前主体；无上下文 → NoPrincipalError。"""
    from mcp.server.auth.middleware.auth_context import get_access_token

    tok = get_access_token()
    if tok is None:
        raise NoPrincipalError("无认证上下文：该工具必须经 MCP 认证通道调用")
    claims = tok.claims or {}
    return Principal(
        user_id=int(tok.subject) if tok.subject else None,
        username=str(claims.get("username") or ""),
        role=str(claims.get("role") or ""),
        tenant_id=str(claims.get("tenant_id") or "default"),
        scopes=frozenset(tok.scopes or ()),
        source=str(claims.get("source") or "token"),
        token_id=claims.get("token_id"),
    )


def requires(*scopes: str) -> Callable:
    """工具级 scope 声明（SDK 只支持服务器级 required_scopes，故此处自研）。

    用法：
        @server.tool(description="…")
        @requires("invoice:write")
        def invoice_ingest(...): ...
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            principal = current_principal()  # 无上下文时先抛 NoPrincipalError
            missing = [s for s in scopes if not principal.has(s)]
            if missing:
                raise ScopeDenied(
                    f"当前令牌缺少所需权限：{'、'.join(missing)}"
                    f"（令牌来源：{principal.source}）。"
                    "请在发票易「我的令牌」中重新签发包含该权限的令牌。"
                )
            return fn(*args, **kwargs)

        return wrapper

    return deco


# 角色中文名（拒绝文案用，与 Web 端菜单口径一致）
_ROLE_ZH = {
    "employee": "普通员工",
    "finance_staff": "财务专员",
    "finance_manager": "财务主管",
    "admin": "系统管理员",
}


def requires_role(*roles: str) -> Callable:
    """工具级角色门——与 @requires 并列的第二维（2026-10-05 修订）。

    用于「REST 同功能为财务/管理专属」的工具：scope 只表达能力粒度，角色表达
    数据归属——回单、发票修改等是全公司数据、无按人收敛，令牌即使被手工授予了
    对应 scope，角色不符仍拒（防御纵深）。

    应置于 @requires 外层（列在上方）：先给出「仅限财务」的清晰拒绝，
    而不是误导性的"缺少 scope、请重新签发令牌"。
    用法：
        @requires_role("finance_staff", "finance_manager", "admin")
        @requires("invoice:write")
        def invoice_update(...): ...
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            principal = current_principal()
            if principal.role not in roles:
                allowed = "、".join(_ROLE_ZH.get(r, r) for r in roles)
                raise RoleDenied(
                    f"该操作仅限{allowed}"
                    f"（当前角色：{_ROLE_ZH.get(principal.role, principal.role)}）。"
                )
            return fn(*args, **kwargs)

        return wrapper

    return deco
