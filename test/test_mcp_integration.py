"""真实 streamable-http 集成：uvicorn 随机端口 + 官方客户端完成 initialize/list_tools/call_tool。

SDK v2 兼容说明：mcp>=2.0.0 的 streamable_http_client 无 headers 参数，官方文档推荐
预配置 httpx2.AsyncClient(headers=...) 携带鉴权头（降级方案）；mcp_types v2 的错误
标记字段名为 is_error（旧版为 isError）。

trust_env=False 说明：httpx/httpx2 经 urllib.request.getproxies() 取代理，macOS 上会
回退读取系统代理配置（scutil）——本机 Clash 类代理（127.0.0.1:7897）对 loopback 目标
返回 502，导致集成测试 initialize 拿到 MCPError。测试只打本机随机端口，必须绕过
系统/环境代理。
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def mcp_url(db):
    # 必须用 create_app() 造**新实例**，不能用模块级 app：SDK 的
    # StreamableHTTPSessionManager 一个实例只能 run() 一次，共用模块级 app 会让
    # 本文件第 2 个测试起服务时抛 RuntimeError（此前本文件只有 1 个测试而掩盖）。
    from invoicing.main import create_app

    port = _free_port()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # 等待就绪
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5, trust_env=False)
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
        headers={"Authorization": f"Bearer {settings.mcp_token}"}, trust_env=False
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


@pytest.mark.asyncio
async def test_personal_token_authenticates_over_http(mcp_url, db):
    """步骤 4 的核心验收：**个人令牌**（非 legacy）能通过 SDK 认证栈并调用工具。

    此前 /mcp 只认单一静态令牌；接入 SDK 认证后，令牌 → 用户身份的链路打通，
    这是 FRD §380「员工只看本人发票」的前置（工具层改身份见步骤 5）。
    """
    from invoicing.models import Role, User
    from invoicing.workflow import mcp_tokens as svc

    emp = User(username="mcp_emp", password_hash="x", role=Role.employee.value)
    db.add(emp)
    db.commit()
    _, plaintext = svc.issue_token(db, emp, name="测试个人令牌", scopes=("invoice:read",))

    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {plaintext}"}, trust_env=False
    )
    try:
        async with streamable_http_client(mcp_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("invoice_list", {"page": 1, "page_size": 5})
                assert result.is_error is False
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_revoked_personal_token_rejected_over_http(mcp_url, db):
    """撤销即时生效（跨进程也成立）：verifier 每次请求查库，无缓存。

    直接断言 HTTP 401——不要用 `pytest.raises` 包 initialize：服务起不来
    （如 session manager 复用）同样会抛异常，那种写法会把失败当通过。
    """
    import httpx2 as _h

    from invoicing.models import Role, User
    from invoicing.workflow import mcp_tokens as svc

    emp = User(username="mcp_emp2", password_hash="x", role=Role.employee.value)
    db.add(emp)
    db.commit()
    row, plaintext = svc.issue_token(db, emp, name="待撤销", scopes=("invoice:read",))

    async with _h.AsyncClient(trust_env=False) as probe:
        before = await probe.post(
            mcp_url, json={}, headers={"Authorization": f"Bearer {plaintext}"}
        )
        assert before.status_code != 401  # 未撤销：认证通过（后续协议错误与鉴权无关）

    svc.revoke_token(db, emp, row.id)

    async with _h.AsyncClient(trust_env=False) as probe:
        after = await probe.post(
            mcp_url, json={}, headers={"Authorization": f"Bearer {plaintext}"}
        )
        assert after.status_code == 401
        assert "WWW-Authenticate" in after.headers  # 规范要求：401 必须带该头
