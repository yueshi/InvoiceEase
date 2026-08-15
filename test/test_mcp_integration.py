"""真实 streamable-http 集成：uvicorn 随机端口 + 官方客户端完成 initialize/list_tools/call_tool。

SDK v2 兼容说明：mcp>=2.0.0 的 streamable_http_client 无 headers 参数，官方文档推荐
预配置 httpx2.AsyncClient(headers=...) 携带鉴权头（降级方案）；mcp_types v2 的错误
标记字段名为 is_error（旧版为 isError）。
"""
import socket
import threading
import time

import httpx
import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from invoicing.config import settings
from invoicing.main import app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def mcp_url(db):
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # 等待就绪
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_full_mcp_session_over_http(mcp_url):
    # SDK v2 以预配置 httpx2.AsyncClient 携带真实 Bearer token
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {settings.mcp_token}"}
    )
    try:
        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert {"invoice_fetch", "invoice_list", "invoice_detail"} <= names
                result = await session.call_tool("invoice_list", {"page": 1, "page_size": 20})
                assert result.is_error is False
    finally:
        await http_client.aclose()
