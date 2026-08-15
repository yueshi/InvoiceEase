# 发票易 MVP MCP Server 实施计划（Plan B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 MCP Server V1：3 个 Tool（invoice_fetch / invoice_list / invoice_detail）通过 streamable-http 挂载在 `/mcp`，静态 Bearer token 鉴权，与 REST 共享 service 层，MCP 触发操作写 `channel=mcp` 审计。

**Architecture:** 官方 MCP Python SDK v2（`mcp.server.mcpserver.MCPServer`）。MCP Server 与 FastAPI 同进程，`streamable_http_app()` 挂载到 `/mcp` 路径，宿主 lifespan 运行 `mcp.session_manager.run()`；纯 ASGI 中间件做 Bearer 鉴权（不缓冲流式响应）。Tool 内部自开 SessionLocal（与 worker 同模式），权限按 admin 语义（合成 User(role=admin) 走 service 层 `_scope_query`，MVP 单租户 + 静态 token 即管理员通道）。

**Tech Stack:** mcp（Python SDK ≥1.9，v2 API）、FastAPI（既有）、pytest + pytest-asyncio（in-memory `Client(mcp)` 协议级测试，asyncio 模式）、uvicorn（集成测试随机端口起真实 HTTP）。

**Spec:** `design/2026-08-14-mvp-design.md` 第 5 节（MCP Tools 定义表 + 关键设计点 1-5）；`frd/InvoiceEase-frd-v0.1.txt` 3.4 节。

## Global Constraints

- 需求基线以 `frd/InvoiceEase-frd-v0.1.txt` 为准；设计文档已评审，冲突时以设计文档为准
- MCP 鉴权：静态 Bearer token（配置项 `MCP_TOKEN`），内网部署够用；Phase 2 升级 OAuth2.0
- REST 与 MCP 共享同一 service 层——MCP Tool 是薄适配器，直接调用 `workflow` service
- MCP 触发的操作写审计日志（`detail.channel=mcp`）；读操作（list/detail）不写审计
- 金额 Decimal 严禁 float；时间戳 fields.utcnow()；中文注释；测试代码在仓库根 `test/`
- 开发/测试环境：SQLite + 本地文件系统 + 进程内队列（无需 docker，不要尝试 docker 命令）
- 每个任务结束 git commit，提交信息格式 `feat(mcp): ...`
- 运行测试：`cd backend && uv run pytest ../test/<file> -v`；全套件 `cd backend && uv run pytest ../test -q`

---

### Task 1: MCP Server 定义与 3 个 Tool（in-memory 协议级测试）

**Files:**
- Create: `backend/src/invoicing/mcp/__init__.py`
- Create: `backend/src/invoicing/mcp/tools.py`
- Create: `backend/src/invoicing/mcp/server.py`
- Create: `test/test_mcp_tools.py`

**Interfaces:**
- Consumes: `services.list_invoices`、`services.get_invoice`（Plan A Task 14）、`poll_mailbox`/`PollResult`（Task 12）、`Mailbox`/`Invoice`/`User`/`Role`（Task 2）、`settings`（Task 1）、`write_audit`（Task 5）、`SessionLocal`
- Produces:
  - `invoicing.mcp.server.build_server() -> MCPServer`（名为「发票易」的 MCP Server，注册 3 个 tool）
  - `mcp` 模块级单例：`mcp = build_server()`（供 main.py 挂载与 in-memory 测试共用）
  - tool 函数：`fetch_invoices(mailbox_id: int | None = None) -> PollResultOut`、`list_invoices_mcp(status=None, date_from=None, date_to=None, keyword=None, page=1, page_size=20) -> InvoiceListResponse`、`get_invoice_mcp(invoice_id: int) -> InvoiceOut`（各自实现于 tools.py，decorator 注册于 server.py）

- [ ] **Step 1: 写失败测试 test/test_mcp_tools.py**

```python
"""MCP 工具测试：直接函数级 + in-memory Client 协议级。"""
from datetime import date
from decimal import Decimal

import pytest
from mcp import Client

from invoicing.db import SessionLocal
from invoicing.mcp.server import mcp
from invoicing.mcp.tools import fetch_invoices, get_invoice_mcp, list_invoices_mcp
from invoicing.models import AuditLog, Invoice, Mailbox, User
from invoicing.security import hash_password


def _seed(db):
    db.add(User(username="caiwu", password_hash=hash_password("pass123"), role="finance_staff"))
    db.add(
        Invoice(
            file_url="a.xml", file_type="XML", invoice_number="24312000000012345678",
            status="pending_submit", total_amount=Decimal("1000.00"),
            seller_name="示例科技有限公司", issue_date=date(2026, 8, 1),
            parse_source="XML", confidence_score=1.0, verify_status="passed",
        )
    )
    db.flush()


def test_list_invoices_mcp(db):
    _seed(db)
    result = list_invoices_mcp(page=1, page_size=20)
    assert result.total == 1
    assert result.items[0].invoice_number == "24312000000012345678"


def test_get_invoice_mcp(db):
    _seed(db)
    with SessionLocal() as s:
        inv_id = s.query(Invoice).first().id
    result = get_invoice_mcp(invoice_id=inv_id)
    assert result.invoice_number == "24312000000012345678"


def test_fetch_invoices_mcp_writes_mcp_audit(db, monkeypatch):
    from invoicing.fetch.service import PollResult

    _seed(db)
    with SessionLocal() as s:
        s.add(Mailbox(
            name="mcp-mailbox", imap_host="127.0.0.1", imap_port=1, use_ssl=False,
            username="u@x.com", password_encrypted="enc:x",
        ))
        s.commit()

    def fake_poll(db2, mailbox, fetcher=None):
        return PollResult(received=1, rejected_images=0, ignored=0, duplicates=0, errors=0)

    monkeypatch.setattr("invoicing.mcp.tools.poll_mailbox", fake_poll)
    result = fetch_invoices(mailbox_id=None)
    assert result.received == 1
    with SessionLocal() as s:
        logs = s.query(AuditLog).filter(AuditLog.action == "FETCH", AuditLog.channel == "mcp").all()
    assert len(logs) == 1


@pytest.mark.asyncio
async def test_in_memory_client_lists_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"invoice_fetch", "invoice_list", "invoice_detail"} <= names
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_mcp_tools.py -v`
Expected: FAIL（`invoicing.mcp` 不存在 / 若 mcp SDK 版本过旧无 `mcp.Client`，报 ImportError 亦为预期失败）

- [ ] **Step 3: 实现 tools.py 与 server.py**

```python
# invoicing/mcp/tools.py
"""MCP 工具实现：薄适配器，直接调用 workflow service 层。"""
from datetime import date
from typing import Any

from invoicing.audit import write_audit
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Invoice, Mailbox, Role, User
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut
from invoicing.workflow import services


def _mcp_admin_user() -> User:
    """MCP 静态 token 即管理员通道：合成 admin 用户走 service 层（无租户过滤，MVP 单租户）。"""
    return User(id=0, username="mcp", password_hash="", role=Role.admin.value)


def fetch_invoices(mailbox_id: int | None = None) -> PollResultOut:
    total = {"received": 0, "rejected_images": 0, "ignored": 0, "duplicates": 0, "errors": 0}
    with SessionLocal() as db:
        q = db.query(Mailbox).filter(Mailbox.enabled.is_(True))
        if mailbox_id is not None:
            q = q.filter(Mailbox.id == mailbox_id)
        mailboxes = q.all()
        if mailbox_id is not None and not mailboxes:
            raise ValueError(f"邮箱不存在或已停用: {mailbox_id}")
        for mb in mailboxes:
            result = poll_mailbox(db, mb)
            for key in total:
                total[key] += getattr(result, key)
        write_audit(
            db, action="FETCH", channel="mcp",
            detail={"mailbox_ids": [mb.id for mb in mailboxes], "result": total},
        )
        db.commit()
    return PollResultOut(**total)


def list_invoices_mcp(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> InvoiceListResponse:
    with SessionLocal() as db:
        return services.list_invoices(
            db, _mcp_admin_user(), status, date_from, date_to, keyword, page, page_size
        )


def get_invoice_mcp(invoice_id: int) -> InvoiceOut:
    with SessionLocal() as db:
        return services.get_invoice(db, _mcp_admin_user(), invoice_id)
```

```python
# invoicing/mcp/server.py
"""MCP Server 装配：3 个 MVP Tool 注册。"""
from datetime import date

from mcp.server.mcpserver import MCPServer

from invoicing.mcp import tools as mcp_tools
from invoicing.schemas.invoice import InvoiceListResponse, InvoiceOut
from invoicing.schemas.mailbox import PollResultOut


def build_server() -> MCPServer:
    server = MCPServer("发票易")

    @server.tool(
        description="手动触发邮箱轮询收取发票，返回收取结果统计（收到/拒收/忽略/重复/错误）。mailbox_id 缺省收取全部启用邮箱。",
    )
    def invoice_fetch(mailbox_id: int | None = None) -> PollResultOut:
        return mcp_tools.fetch_invoices(mailbox_id)

    @server.tool(
        description="查询发票列表：按状态/开票日期区间/关键词（发票号码或购销方名称）筛选，分页返回。",
    )
    def invoice_list(
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> InvoiceListResponse:
        return mcp_tools.list_invoices_mcp(status, date_from, date_to, keyword, page, page_size)

    @server.tool(description="查看单张发票完整信息（结构化字段+状态+验真结果）。")
    def invoice_detail(invoice_id: int) -> InvoiceOut:
        return mcp_tools.get_invoice_mcp(invoice_id)

    return server


mcp = build_server()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_mcp_tools.py -v`
Expected: 4 PASS（若 `mcp.Client` in-memory API 与已安装版本不符，按已安装版本的等价写法调整测试并如实记录——查看 `.venv/lib/python3.11/site-packages/mcp/` 下 client 实现；若该版本无 in-memory Client，协议级测试降级为「直接调 tool 函数」并在报告说明，挂载后的协议验证由 Task 3 集成测试兜底）

- [ ] **Step 5: 回归全套件并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 89 passed（85 + 4）

```bash
git add backend/src/invoicing/mcp test/test_mcp_tools.py
git commit -m "feat(mcp): MCP Server 定义与 3 个 Tool（in-memory 测试）"
```

---

### Task 2: /mcp 挂载 + 静态 token 鉴权中间件 + HTTP 冒烟测试

**Files:**
- Modify: `backend/src/invoicing/main.py`（lifespan 运行 `mcp.session_manager.run()`；挂载 `/mcp`；注册鉴权中间件）
- Create: `backend/src/invoicing/mcp/auth.py`（纯 ASGI Bearer 鉴权中间件）
- Create: `test/test_mcp_http.py`

**Interfaces:**
- Consumes: `invoicing.mcp.server.mcp`（Task 1）、`settings.mcp_token`、既有 lifespan（ensure_admin_user + scheduler）
- Produces:
  - `MCPAuthMiddleware(app)`：`path.startswith("/mcp")` 且 `Authorization: Bearer <MCP_TOKEN>`（hmac.compare_digest 恒定时间比较）不匹配 → 401 JSON `{code, message}`；其余路径与合法请求直接透传（纯 ASGI，无缓冲，兼容 SSE 流式）
  - `create_app()` 挂载 `app.mount("/mcp", mcp.streamable_http_app(json_response=True))`；lifespan 合并既有逻辑 + `async with mcp.session_manager.run():`

- [ ] **Step 1: 写失败测试 test/test_mcp_http.py**

```python
"""MCP HTTP 冒烟：鉴权 401/通过、挂载可达、其他路径不受影响。"""
import pytest
from fastapi.testclient import TestClient

from invoicing.config import settings
from invoicing.db import get_db
from invoicing.main import create_app


@pytest.fixture()
def client(db):
    app = create_app()

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c


def test_mcp_no_token_401(client):
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401


def test_mcp_wrong_token_401(client):
    resp = client.post("/mcp", json={}, headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_mcp_valid_token_passes_auth(client):
    resp = client.post(
        "/mcp", json={}, headers={"Authorization": f"Bearer {settings.mcp_token}"}
    )
    assert resp.status_code != 401  # 进入 MCP 协议层后的响应（如 400/406），鉴权已放行


def test_other_paths_unaffected(client):
    assert client.get("/health").status_code == 200
    # /api/v1 未登录仍是 REST 的 401（非 MCP 中间件）
    assert client.get("/api/v1/invoices").status_code == 401
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_mcp_http.py -v`
Expected: FAIL（/mcp 未挂载，404 ≠ 401）

- [ ] **Step 3: 实现 mcp/auth.py 与 main.py 修改**

```python
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
            expected = f"Bearer {settings.mcp_token}".encode()
            if not hmac.compare_digest(auth.encode(), expected):
                response = JSONResponse(
                    {"code": "unauthorized", "message": "MCP token 无效"}, status_code=401
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
```

main.py 修改（完整新版）：

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from invoicing.api import api_router
from invoicing.bootstrap import ensure_admin_user
from invoicing.db import SessionLocal
from invoicing.mcp.auth import MCPAuthMiddleware
from invoicing.mcp.server import mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_admin_user(db)
    from invoicing import scheduler as scheduler_mod

    scheduler_mod.setup_scheduler(app)
    # MCP 挂载模式下宿主负责运行 session_manager（SDK v2 要求）
    async with mcp.session_manager.run():
        yield
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="发票易 InvoiceEase", lifespan=lifespan)
    app.add_middleware(MCPAuthMiddleware)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    app.include_router(api_router)
    app.mount("/mcp", mcp.streamable_http_app(json_response=True))
    return app


app = create_app()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_mcp_http.py -v`
Expected: 4 PASS

- [ ] **Step 5: 回归全套件并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 93 passed（89 + 4；test_health 的 lifespan 现在包含 session_manager，若出现与 SQLite/事件循环相关的异常按报错修复并记录）

```bash
git add backend/src/invoicing/main.py backend/src/invoicing/mcp/auth.py test/test_mcp_http.py
git commit -m "feat(mcp): /mcp 挂载与静态 token 鉴权中间件"
```

---

### Task 3: streamable-http 端到端协议集成测试 + 文档

**Files:**
- Create: `test/test_mcp_integration.py`
- Modify: `docs/开发环境指南.md`（追加 MCP 端点与 WorkBuddy 接入说明）
- Modify: `CLAUDE.md`（构建与测试一节追加 MCP 集成测试命令说明）

**Interfaces:**
- Consumes: Task 2 的完整 app
- Produces: 真实 HTTP 上的 MCP 协议会话测试（initialize → list_tools → call_tool）+ 接入文档

- [ ] **Step 1: 写失败测试 test/test_mcp_integration.py**

```python
"""真实 streamable-http 集成：uvicorn 随机端口 + 官方客户端完成 initialize/list_tools/call_tool。"""
import socket
import threading

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
def mcp_url():
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # 等待就绪
    import time

    import httpx

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
    headers = {"Authorization": f"Bearer {settings.mcp_token}"}
    async with streamable_http_client(mcp_url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"invoice_fetch", "invoice_list", "invoice_detail"} <= names
            result = await session.call_tool("invoice_list", {"page": 1, "page_size": 20})
            assert result.isError is False
```

（注意：若已安装 SDK 版本的 `streamable_http_client` 不支持 `headers` 参数，改为临时环境变量方案——在 fixture 内 monkeypatch `invoicing.mcp.auth.settings.mcp_token` 为客户端可携带的空 token 前先验证无 token 401（HTTP 层已由 Task 2 覆盖），协议集成聚焦 initialize/list_tools/call_tool 主路径，并在报告中说明 SDK 版本限制。）

- [ ] **Step 2: 运行测试，确认失败或通过**

Run: `cd backend && uv run pytest ../test/test_mcp_integration.py -v`
Expected: 通过（Task 2 已完成）；若失败按报错修复——常见问题：事件循环与 threading 混用（anyio 测试内 uvicorn 线程 OK）、session_manager 与测试库连接。修复后在报告记录。

- [ ] **Step 3: 文档更新**

docs/开发环境指南.md 在「启动服务」之后追加：

```markdown
## MCP 端点（WorkBuddy 接入）

- 端点：`http://<host>:8000/mcp`（streamable-http）
- 鉴权：`Authorization: Bearer <INVOICING_MCP_TOKEN>`（环境变量，默认 change-me，生产必须改）
- 工具：`invoice_fetch`（手动触发收取）/ `invoice_list`（列表查询）/ `invoice_detail`（详情）
- 验证（官方 SDK 客户端）：

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async with streamable_http_client("http://localhost:8000/mcp", headers={"Authorization": "Bearer <token>"}) as (r, w):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await session.list_tools()
```

WorkBuddy 中在 MCP Server 配置处填入上述端点和 Bearer token 即可接入。
```

CLAUDE.md 构建与测试节追加一行：

```bash
cd backend && uv run pytest ../test/test_mcp_integration.py -v  # MCP 协议级集成测试（需端口可用）
```

- [ ] **Step 4: 全量回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 94 passed（93 + 集成 1）

```bash
git add test/test_mcp_integration.py docs/开发环境指南.md CLAUDE.md
git commit -m "test(mcp): streamable-http 协议级集成测试与接入文档"
```

---

## Plan B 验收清单（全部完成后核对）

- [ ] `uv run pytest ../test -q` 全绿（94 用例左右）
- [ ] 3 个 Tool 与 FRD 3.4.2 定义一致（名称/参数/返回）
- [ ] `/mcp` 无 token/错 token 401；对 token 放行（Task 2 测试）
- [ ] 真实 HTTP 协议会话可用：initialize → list_tools → call_tool（Task 3 集成测试）
- [ ] MCP 触发收取写 `channel=mcp` 审计（Task 1 测试）
- [ ] REST 与 MCP 共享 service 层（tool 实现直调 services，无重复逻辑）
