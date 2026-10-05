# backend/src/invoicing/agent/tools_bridge.py
"""把 39 个 MCP 工具包成 AgentTool（嵌入式直调，非 MCP 协议层）。

身份注入：SDK 的 auth_context_var（生产由 AuthContextMiddleware 设置，
测试由 conftest.mcp_auth fixture 设置）——这里用同一机制注入「当前登录用户」，
scopes 取 ROLE_DEFAULT_SCOPES[role]，工具层 @requires 照常守门。
"""
import json

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from invoicing.agent.core.tools import AgentToolResult
from invoicing.mcp.identity import ROLE_DEFAULT_SCOPES


def _result_to_text(result) -> str:
    """CallToolResult → LLM 可见文本（content 块拼接）。

    convert_result 总是先构造 unstructured content（pydantic/ dict / list
    会被 JSON 序列化成 TextContent），所以读 content 块即可；
    structuredContent 只在 content 为空时兜底。
    """
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    if not parts:
        structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
        if structured is not None:
            parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return "\n".join(parts) or "(无输出)"


class McpToolAdapter:
    """单个 MCP 工具 → AgentTool 协议适配。

    __init__ 立即把 user 拍平成原语（不持有 ORM 对象——请求结束后 session 关闭）。
    """

    def __init__(self, tool_info, *, user_id: int, username: str, role: str, tenant_id: str = "default"):
        self.name: str = tool_info.name
        self.description: str = tool_info.description or ""
        self.schema: dict = tool_info.input_schema or {"type": "object", "properties": {}}
        self._user_id = user_id
        self._username = username
        self._role = role
        self._tenant_id = tenant_id
        self._scopes = tuple(ROLE_DEFAULT_SCOPES.get(role, ()))

    async def execute(self, tool_call_id, args, cancel_event, update_callback) -> AgentToolResult:
        from invoicing.mcp.server import mcp

        if cancel_event is not None and cancel_event.is_set():
            return AgentToolResult.text("[cancelled] 已取消", is_error=True)

        token = auth_context_var.set(AuthenticatedUser(AccessToken(
            token="agent-embedded", client_id="web-agent",
            scopes=list(self._scopes),
            subject=str(self._user_id),
            claims={
                "username": self._username, "role": self._role,
                "tenant_id": self._tenant_id, "source": "agent", "token_id": None,
            },
        )))
        try:
            result = await mcp.call_tool(self.name, args)
            # 字段名双读：本仓库锁定的 mcp 2.0.0 实为 is_error（isError 是旧名，留作兼容）
            return AgentToolResult.text(
                _result_to_text(result),
                is_error=bool(getattr(result, "is_error", getattr(result, "isError", False))),
            )
        except Exception as e:
            # SDK 把工具体异常包成 ToolError（如 ScopeDenied / ValueError 直译）
            return AgentToolResult.text(f"[tool_exception] {type(e).__name__}: {e}", is_error=True)
        finally:
            auth_context_var.reset(token)


async def build_tools_for_user(user) -> list[McpToolAdapter]:
    """当前用户可用工具（调用前按权限过滤，2026-10-05）。

    过滤 = scope 集 ⊆ 角色预设 ∩ 角色白名单（数据源为 @requires/@requires_role
    的装饰器元数据），使 LLM 的工具清单与用户权限一致——不再先调用再吃
    RoleDenied/ScopeDenied。执行层的门保持不动，作为纵深防御。
    """
    from invoicing.mcp.server import mcp
    from invoicing.mcp.tools import permissions_of

    scopes = frozenset(ROLE_DEFAULT_SCOPES.get(user.role, ()))
    infos = await mcp.list_tools()
    allowed: list[McpToolAdapter] = []
    for info in infos:
        need_scopes, need_roles = permissions_of(info.name)
        if not need_scopes <= scopes:
            continue
        if need_roles and user.role not in need_roles:
            continue
        allowed.append(
            McpToolAdapter(info, user_id=user.id, username=user.username, role=user.role)
        )
    return allowed
