# invoicing/mcp/auth.py
"""MCP 静态 Bearer token 鉴权（纯 ASGI 中间件，透传不缓冲，兼容 SSE 流式）。"""
import hmac

from starlette.responses import JSONResponse

from invoicing.config import settings


class MCPAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("latin-1")
            # 与 header 的 latin-1 解码往返一致（token 为 ASCII 配置值）
            expected = f"Bearer {settings.mcp_token}".encode("latin-1")
            if not hmac.compare_digest(auth.encode(), expected):
                response = JSONResponse(
                    {"code": "unauthorized", "message": "MCP token 无效"}, status_code=401
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
