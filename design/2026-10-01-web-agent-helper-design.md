# Web Agent 助手设计方案

- **日期**：2026-10-01
- **状态**：草案，待用户审阅
- **范围**：InvoiceEase 新增内嵌 Web Agent 助手（自然语言驱动 36 个 MCP 工具）
- **参考实现**：AuditMind-0928/auditmind/agent（R1 重构后的 main_agent 单循环架构）

---

## 1. 背景与目标

InvoiceEase 已实现 36 个 MCP 工具（`backend/src/invoicing/mcp/server.py:35-249`），但用户只能通过 Web UI 表单或 MCP 客户端（WorkBuddy 等）触发。**目标**是新增一个内嵌于 Web 管理后台的 Agent 助手：

1. 用户用自然语言表达诉求，Agent 自动挑选并执行合适 MCP 工具
3. 流式回传 token 与工具执行过程，用户可中途取消
4. 多轮会话历史与上下文延续
5. 严格遵循现有 RBAC、scoped_invoices 数据隔离、audit_logs 审计、离线部署约束

---

## 2. 关键决策（已与用户确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| MCP 工具接入方式 | **Embedded** —— Agent 后端直接 `import` MCP 函数 | 零网络开销、复用 Principal/scope/scopinvoices |
| 会话历史存储 | **新建** `agent_sessions` / `agent_messages` 两表 | 与 audit_logs 解耦，支持侧栏/历史回看 |
| 前端形态 | **全局抽屉 + 悬浮球**（AuditMind 原型） | 不打断当前页面工作流 |
| 使用资格 | **所有登录用户都能用**，但工具集被本人 scope 锁死 | 「Agent 是用户手」，符合最小授权原则 |

---

## 3. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (Vue 3 + Pinia + antdv4)                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ FloatingButton → AgentDrawer (侧栏)                  │   │
│  │   └─ MessageList (markdown + tool_call 状态机)       │   │
│  │   └─ MessageInput (含取消按钮)                       │   │
│  │   └─ ContextChip (显示页面上下文：发票 ID / 报销单)  │   │
│  └──────────────────────────────────────────────────────┘   │
│         │ POST /api/v1/agent/sessions/{id}/messages         │
│         │   Authorization: Bearer <JWT>                     │
│         │   Accept: text/event-stream                       │
│         ▼                                                  │
│ ┌────────────────────────────────────────────────────────┐  │
│  │ invoicing/api/agent.py                                │  │
│  │   Depends(get_current_user)  ← JWT, SUSPENDED 闸门     │  │
│  │   StreamingResponse(agent_sse_emitter.stream())       │  │
│  │       │                                                │  │
│  │       ▼                                                │  │
│  │ invoicing/agent/main_agent.py                         │  │
│  │   run_agent(message, ctx, emitter, profile)           │  │
│  │       │                                                │  │
│  │       ▼                                                │  │
│  │ invoicing/agent/core/loop.py (async generator)        │  │
│  │   for turn in range(max_turns):                       │  │
│  │     driver.stream_chat(messages, system_prompt)       │  │
│  │     ──> if no tool_calls: stop                        │  │
│  │     ──> else: _execute_tools_parallel(tool_calls)     │  │
│  │           └─→ invoicing/mcp/tools.py (Embedded 调)    │  │
│  │           └─→ Principal 由当前 user.scopes 现场构造   │  │
│  └────────────────────────────────────────────────────────┘  │
│         │                                                  │
│         ▼                                                  │
│  ┌─────────────────────────┐   ┌──────────────────────┐     │
│  │ PostgreSQL/SQLite       │   │ LLM endpoint         │     │
│  │ - users (现有)          │   │ - 在线: dashscope    │     │
│  │ - invoices / claims …   │   │ - 离线: 内网 vLLM    │     │
│  │ - audit_logs (现有)     │   │   (offline_deploy=T) │     │
│  │ + agent_sessions (新)   │   └──────────────────────┘     │
│  │ + agent_messages (新)   │                               │
│  └─────────────────────────┘                                │
└─────────────────────────────────────────────────────────────┘
```

### 数据流（一轮多步示例）

用户问「列出本月待复核的发票并提交报销单 #42」：

1. 前端 POST `/api/v1/agent/sessions/{id}/messages` 带 `message` + `context={claim_id:42}`
3. 后端 `get_current_user` 校验 JWT，构造 `AgentProfile`
4. `run_agent()` 加载会话历史（最近 50 轮/8000 token）→ 进入循环
5. 第 1 轮：调 LLM 流式收 token → LLM 输出 `{"tool_call":[{"name":"invoice_list",...}]}` → emit `tool_call start`
6. `tools_bridge.execute()` 现场构造 Principal（user.scopes）→ 调 `mcp/tools.py:invoice_list()` → emit `tool_call done`
7. 工具结果回灌为 user-role message → 第 2 轮再调 LLM
8. 循环直到 LLM 不再发 tool_call → emit `done`
9. 流完主流程 → 后台 task 写 `agent_messages` + `audit_logs(action=AGENT_CHAT, channel="web")`

---

## 4. 模块清单（后端新增 `invoicing/agent/`）

```
invoicing/agent/
├── __init__.py
├── main_agent.py            # run_agent() 入口（照搬 AuditMind 同名）
├── profile.py               # AgentProfile = system_prompt + tools_factory + max_turns
├── llm.py                   # 复用 parse/llm.py 的 OpenAI 客户端，加 chat/astream
├── history.py               # 读写 agent_sessions / agent_messages；按 max_turns/max_tokens 截断
├── observability.py         # 写 audit_logs (action=AGENT_CHAT)
├── sse.py                   # AgentSSEEmitter + 6 类事件
├── tools_bridge.py          # 把 36 个 MCP tool 包成 AgentTool 列表（Embedded）
├── system_prompts.py         # 默认 system prompt（发票易场景）
└── core/
    ├── events.py            # 8 层 dataclass（Start/Turn/Message/Tool 等）
    ├── context.py           # AgentContext（page / invoice_id / claim_id / period …）
    ├── loop.py              # run_agent_loop + WRAPUP 收尾轮
    ├── tools.py             # AgentTool Protocol + AgentToolResult
    ├── driver.py            # OpenAI Driver 适配器（JSON 协议，Qwen 兼容）
    ├── guards.py            # capability_guard（兜底校验）
    └── sse_bridge.py        # 8 层 → 6 类 SSE 事件桥接
```

**复用现有模块（不新建）**：
- `invoicing/security.py:get_current_user` + SUSPENDED 闸门
- `invoicing/audit.py:write_audit`
- `invoicing/parse/llm.py` 的 OpenAI 客户端 + `settings.llm_*`
- `invoicing/mcp/identity.py:Principal` + `@requires(scope)`
- `invoicing/permissions.py:ROLE_ACTIONS`
- `invoicing/scheduler.py:register_task`（会话保留 cron）
- `invoicing/ops/checks.py`（启动自检加 agent 配置校验）

**AuditMind 适配原则**：
- **照搬**：`core/{events,context,loop,guards,tools,sse_bridge,driver 框架}.py`、`sse.py`、`profile.py`
- **重写**：`main_agent.py` 的 system prompt（发票场景）；`tools_bridge.py`（指向 MCP 工具）；`history.py`（换表名）；`llm.py`（用现有 parse/llm.py 客户端）
- **不照搬**：`intents.py`、`workflows/*`（AuditMind 已废弃层）

---

## 5. LLM Driver（`agent/core/driver.py`）

照搬 AuditMind 框架，仅替换客户端实现：

```python
class InvoiceEaseDriver:
    def __init__(self, client: OpenAI):
        self._client = client  # parse/llm.py 的 OpenAI 实例

    async def stream_chat(self, messages, system_prompt) -> AsyncIterator[str]:
        async for chunk in self._client.chat.completions.create(
            model=settings.agent_llm_model or settings.llm_model_text,
            messages=[{"role":"system","content": system_prompt}, *messages],
            stream=True, temperature=settings.agent_llm_temperature,
            timeout=settings.llm_timeout_seconds,
        ):
            yield chunk.choices[0].delta.content or ""

    async def chat(self, messages, system_prompt) -> str:
        resp = await self._client.chat.completions.create(
            model=settings.agent_llm_model or settings.llm_model_text,
            messages=[{"role":"system","content": system_prompt}, *messages],
            timeout=settings.llm_timeout_seconds,
        )
        return resp.choices[0].message.content
```

**Tool 调用协议**：JSON 协议（不依赖 OpenAI 原生 `tools=`），LLM 输出 `{"tool_call":[{"name":"...","arguments":{...}}]}`，`_parse_assistant_reply` 解析。理由：兼容 Qwen，避开原生 tool_use 不稳。

---

## 6. Tool Bridge（`agent/tools_bridge.py`）

```python
class McpToolAdapter:
    """把 invoicing.mcp.tools 下的 36 个函数包成 AgentTool。"""
    def __init__(self, name, description, schema, mcp_func, scopes: list[str]):
        self.name = name; self.description = description; self.schema = schema
        self._mcp_func = mcp_func; self._scopes = scopes

    async def execute(self, tool_call_id, args, cancel_event):
        from invoicing.mcp.identity import Principal, _PRINCIPAL
        token = _PRINCIPAL.set(Principal(
            user_id=self._user_id, username=self._username,
            role=self._role, tenant_id=self._tenant_id,
            scopes=self._scopes, source="agent", token_id=None,
        ))
        try:
            result = await self._mcp_func(**args)
            return AgentToolResult.text(result, is_error=False)
        except ScopeDenied as e:
            return AgentToolResult.text(f"[SCOPE_DENIED] {e}", is_error=True)
        finally:
            _PRINCIPAL.reset(token)
```

**默认工具全集 = 36**（按用户 scopes 过滤由 `@requires(scope)` 在 MCP 工具层守）。LLM 只能看到 schema，调用时由 MCP 守门。

---

## 7. Loop 与收尾（`agent/core/loop.py`）

照搬 AuditMind 模式：

- `max_turns = settings.agent_max_steps`（默认 **8**；AuditMind 是 10，发票场景步骤更短）
- `max_context_messages = settings.agent_max_context_messages`（默认 **50**）
- `max_context_tokens = settings.agent_max_context_tokens`（默认 **8000**）
- **WRAPUP 收尾轮**：`max_turns` 触顶且当次未产可见文本 → 注入"基于以上信息给出结论"再调一次
- **capability_guard**：检测 LLM 输出非白名单工具名 → 当作纯文本回答，不死循环
- **终止条件**：`no_more_tool_calls` / `max_turns` / `cancelled` / `hook_stop`（guard 命中）/ `error`

---

## 8. SSE 协议（`agent/sse.py`）

6 类事件与 AuditMind 一致，前端可零迁移复用：

| 事件 | payload | 触发 |
|---|---|---|
| `intent` | `{kind, confidence}` | run 启动 |
| `token` | `{text}` | LLM 流式 token |
| `tool_call` | `{tool, status, ms}` | start/done/failed |
| `proposal` | `{title}` | 给会话自动起标题 |
| `done` | `{usage:{prompt,output_tokens}, stop_reason}` | run 收尾 |
| `error` | `{code, message}` | LLM/工具/MCP 任一异常 |

**错误码**（落 `audit_logs.error_code`）：`LLM_TIMEOUT` / `LLM_EMPTY` / `TOOL_EXCEPTION` / `SCOPE_DENIED` / `AGENT_INTERNAL` / `OFFLINE_BLOCKED`.

---

## 9. 历史存储（`agent/history.py`）

### 9.1 Alembic 迁移

```sql
CREATE TABLE agent_sessions (
    id INTEGER PK,
    user_id INTEGER NOT NULL REFERENCES users(id) ONDELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    title VARCHAR(120),
    created_at DATETIME NOT NULL DEFAULT now,
    updated_at DATETIME NOT NULL DEFAULT now,
    archived_at DATETIME NULL
);
CREATE INDEX ix_agent_sessions_user ON agent_sessions(user_id, updated_at DESC);

CREATE TABLE agent_messages (
    id INTEGER PK,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ONDELETE CASCADE,
    role VARCHAR(16) NOT NULL,            -- user / assistant / tool
    content TEXT,
    tool_calls JSON,                     -- LLM 当轮发出的 tool_call 列表
    tool_results JSON,                   -- 工具执行结果
    created_at DATETIME NOT NULL DEFAULT now,
    duration_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER
);
CREATE INDEX ix_agent_messages_session ON agent_messages(session_id, id);
```

### 9.2 加载策略

照搬 AuditMind `load_session_history`：先按 `max_context_messages=50` 限轮数，再按 `max_context_tokens=8000` 从最老整轮 pop。`tool` 角色走 `user` 伪装回灌（Qwen 兼容）。

---

## 10. 配置项（`config.py` 新增）

```python
# Agent 开关与运行参数
agent_enabled: bool = True
agent_max_steps: int = 8
agent_max_context_messages: int = 50
agent_max_context_tokens: int = 8000
agent_llm_model: str = ""                          # 空 = 复用 llm_model_text
agent_llm_temperature: float = 0.3
agent_stream_idle_timeout_seconds: int = 60
agent_session_retention_days: int = 90

# 离线约束（复用现有 offline_deploy + llm_*，新增下列两个开关）
agent_require_offline_llm: bool = True             # offline_deploy=T 且 llm_base_url 指向公网 → 拒绝启动
agent_block_cloud_llm_domains: list[str] = [         # 公网域名白名单（启动自检 BLOCK 项）
    "dashscope.aliyuncs.com",
    "api.openai.com",
    "anthropic.com",
]
```

**启动自检**（`ops/checks.py` 新增）：
- `agent_enabled=True` 且 `llm_enabled=False` → **WARN**（运行时 503）
- `offline_deploy=True` 且 `llm_base_url` 命中 `agent_block_cloud_llm_domains` → **BLOCK** 启动

---

## 11. 鉴权 / 审计 / SUSPENDED 闸门

### 11.1 API 入口

```python
# invoicing/api/agent.py + invoicing/schemas/agent.py

# Request/Response 形态（schemas/agent.py）
class ChatRequest(BaseModel):
    message: str                            # 用户原话
    context: dict | None = None             # 页面上下文：{page, invoice_id, claim_id, period, ...}

class CreateSessionRequest(BaseModel):
    title: str | None = None

# 路由
@router.post("/sessions/{session_id}/messages")
async def chat(session_id: int, body: ChatRequest,
               user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    # 1. 会话归属校验：session.user_id == user.id（admin 例外）
    # 2. profile = AgentProfile(system=..., tools_factory=lambda: tools_for_user(user))
    # 3. emitter + run_agent(user, session_id, body.message, body.context)
    # 4. StreamingResponse(emitter.stream(),
    #                      headers={"Cache-Control":"no-cache",
    #                               "X-Accel-Buffering":"no"})
    # 5. 流完后在后台 task 写 agent_messages + audit_logs
```

### 11.2 审计

```python
# agent/observability.py
audit.write_audit(db, action="AGENT_CHAT",
                  user_id=user.id, ip_address=request.client.host,
                  channel="web",
                  detail={"session_id": session_id,
                          "tool_calls": [t.name for t in tool_calls],
                          "input_tokens": usage.prompt,
                          "output_tokens": usage.completion,
                          "duration_ms": duration,
                          "error_code": error_code})
```

**SUSPENDED 闸门**：由 `Depends(get_current_user)` 现有逻辑 `security.py:65-66` 把关，user.status=SUSPENDED 立即 401，前端跳登录页。

---

## 12. 会话保留（`scheduler.py` 新增 cron）

复用 `audit_retention` 套路：
- `agent_retention` cron 每日 03:30
- 删除 `archived_at < now - retention_days` 或 `updated_at < now - retention_days AND no archived` 的会话
- ON DELETE CASCADE 自动清掉 `agent_messages`

---

## 13. 错误处理矩阵

| 故障 | 行为 | 前端表现 |
|---|---|---|
| `llm_enabled=False` | 后端 503 + `error.code: llm_disabled` | 抽屉顶部 banner「智能助手未启用」 |
| LLM 超时 | abort loop + emit `error` | 助手消息变红色，可点「重试」 |
| LLM 空响应 | 注入 WRAPUP_INSTRUCTION 再调一次 | 用户无感 |
| Tool scope 不足 | `ScopeDenied` → `AgentToolResult.is_error=True` | 助手消息中提示「权限不足」 |
| Tool 抛异常 | 同上，error_code=TOOL_EXCEPTION | 同上 |
| 离线部署 + 公网域名 | 启动 BLOCK（`ops/checks.py`） | 服务根本起不来 |
| DB 写失败 | 流已发出，落库失败 → stderr 日志 + 监控告警 | 用户无感，下一轮会丢上下文 |
| 用户 SUSPENDED | 现成闸门 → 401 | 跳登录页 |
| 写操作工单（expense_submit/invoice_delete 等） | LLM 输出 tool_call 后前端弹「将执行：...，确认？」 | 用户点确认后才真发，避免误删 |

---

## 14. 前端布局

### 14.1 目录结构

```
web/src/agent/                              # 新增模块（参考 AuditMind/frontend/src/agent/）
├── api.ts                                  # fetchEventStream + parseSSEChunk
├── types.ts                                # AgentSession / AgentMessage / AgentToolCall / SSEEvent
├── store.ts                                # Pinia：sessions / currentSessionId / messages / streamingMessage
├── components/
│   ├── AgentDrawer.vue                     # 抽屉主容器（与 McpTokensView 风格一致）
│   ├── FloatingButton.vue                  # 右下角悬浮球（全局挂载在 App.vue）
│   ├── MessageList.vue                     # 消息列表
│   ├── MessageInput.vue                    # 输入区（含取消按钮 + 重试按钮）
│   ├── AssistantMarkdown.vue               # 助手回复渲染（含 tool_call 折叠块）
│   └── ContextChip.vue                     # 上下文 chip（发票 ID / 报销单 / 期间）
└── __tests__/
    ├── store.test.ts
    └── MessageList.test.ts
```

### 14.2 Pinia store 状态

```ts
interface AgentState {
  drawerOpen: boolean
  floatingButtonVisible: boolean           // 登录后 true，登出后 false
  sessions: AgentSession[]
  currentSessionId: number | null
  messages: AgentMessage[]                 // 当前会话的消息
  streamingMessage: {                      // 当轮正在流的助手消息
    content: string
    toolCalls: AgentToolCall[]             // start → done/failed
    finished: boolean
  } | null
  error: { code: string; message: string } | null
  abortController: AbortController | null
}
```

**关键 action**：`openDrawer` / `closeDrawer` / `createSession` / `loadSessions` / `loadMessages` / `sendMessage(text, context)` / `cancel` / `retry`.

### 14.3 SSE 解析（`agent/api.ts`）

```typescript
export async function* fetchEventStream(
  url: string, body: unknown, signal: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const resp = await axios.post(url, body, {
    responseType: 'stream', signal,
    headers: { Accept: 'text/event-stream', 'Cache-Control': 'no-cache' },
  })
  const reader = resp.data.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const dataLine = frame.split('\n').find(l => l.startsWith('data:'))
      if (!dataLine) continue
      try { yield JSON.parse(dataLine.slice(5).trim()) as SSEEvent }
      catch { /* 跳过空/非 JSON 帧 */ }
    }
  }
}
```

### 14.4 路由与全局挂载

```typescript
// web/src/router/index.ts 新增
{ path: '/agent', component: AgentChatView, meta: { title: '智能助手' } }

// web/src/App.vue：登录后渲染 <FloatingButton /> 与 <AgentDrawer />
// 监听 useAuthStore.user —— 登出后强制 closeDrawer + 清空 messages

// Context 注入：其它页面跳抽屉时
router.push({ name: 'agent', query: { invoice_id, period } })
// 抽屉打开时从 query 解析并塞入 sendMessage 的 context
```

---

## 15. 测试策略

### 15.1 后端（`test/` 新增）

| 文件 | 覆盖 |
|---|---|
| `test_agent_loop.py` | max_turns 终止、tool 异常处理、WRAPUP 触发 |
| `test_agent_driver.py` | `_probe_stream_buffer` 各种 LLM 输出格式（裸 JSON / 前导 NL+JSON / fence 滥用 / 空响应） |
| `test_agent_tools_bridge.py` | `ScopeDenied` 包成 `AgentToolResult(is_error=True)` |
| `test_agent_history.py` | max_turns/max_tokens 截断 + 整轮 pop |
| `test_agent_sse.py` | 6 类事件序列化 |
| `test_agent_api_integration.py` | 临时 user + session + SSE 流式响应 + audit_logs 写入验证 |

### 15.2 前端（`web/src/agent/__tests__/`）

- `store.test.ts` — Pinia store：token 合并、tool_call 状态机、cancel、retry
- `MessageList.test.ts` — 组件：tool_call done 时显示对号 + 耗时

### 15.3 E2E 冒烟（一次性手动）

1. 启动 → 登录 → 点悬浮球 → 输入「列出本月待复核发票」 → 流式收 token → tool_call invoice_list → 看到助手回复 + tool_call done
2. 切到 employee 身份 → 抽屉内问「删除发票 #123」 → 应见 SCOPE_DENIED 错误码 + tool_call failed
3. 离线模式：`OFFLINE_DEPLOY=true` 且 `INVOICING_LLM_BASE_URL=dashscope` → 服务拒绝启动

---

## 16. 实施步骤（按 PR 分批）

```
PR1 后端核心
  ├─ alembic 新建 agent_sessions + agent_messages 表
  ├─ invoicing/agent/ 目录：events / tools / driver 骨架（无 system prompt）
  ├─ config.py 新增 agent_* 配置项
  ├─ ops/checks.py 加 agent 配置自检
  ├─ test/test_agent_driver.py + test/test_agent_loop.py
  └─ ✅ 冒烟：脚本直接 run_agent 跑单轮就能拿到 done

PR2 工具桥
  ├─ agent/tools_bridge.py: McpToolAdapter × 36
  ├─ agent/main_agent.py: run_agent() 入口
  ├─ agent/history.py + observability.py
  ├─ test/test_agent_tools_bridge.py + test/test_agent_api_integration.py
  └─ ✅ 冒烟：curl POST /api/v1/agent/sessions/{id}/messages 流式收 token

PR3 API + 鉴权
  ├─ invoicing/api/agent.py: router + SSE StreamingResponse
  ├─ api/__init__.py include_router
  ├─ audit_logs 写入验证
  ├─ test/test_agent_api_integration.py 加鉴权用例
  └─ ✅ 冒烟：JWT + SUSPENDED 闸门路径

PR4 前端 Agent 模块
  ├─ web/src/agent/api.ts + types.ts + store.ts
  ├─ agent/components/* (FloatingButton / AgentDrawer / MessageList / MessageInput / AssistantMarkdown / ContextChip)
  ├─ App.vue 挂载 + router 加 /agent
  ├─ web/src/agent/__tests__/*
  └─ ✅ 冒烟：抽屉点开 → 输入 → 流式回复

PR5 会话侧栏与上下文
  ├─ 会话列表（左栏）+ 自动标题
  ├─ context chip 从 router.query 注入
  ├─ scheduler 加 agent_retention cron
  └─ ✅ 冒烟：跨会话切换不丢消息

PR6 离线部署闸门 + 监控
  ├─ ops/checks.py BLOCK 项落地（offline + 公网域名）
  ├─ ops_alerts: agent error rate > 20% 触发告警
  └─ ✅ 冒烟：OFFLINE_DEPLOY=true 且 llm_base_url=dashscope → 启动失败
```

---

## 17. 风险与权衡

| 风险 | 缓解 |
|---|---|
| LLM 输出格式漂移导致 loop 假终止 | `_probe_stream_buffer` 三层 fallback + capability_guard 拦截未知工具 |
| 36 个工具 schema 进 prompt 把 token 打爆 | 默认把 `invoice:* / expense:* / report:read` 高频 12 个装入系统 prompt；其余按需「工具按作用域懒披露」（PR2 后续优化） |
| LLM 在离线部署时直连云端 | `ops/checks.py` 启动 BLOCK；前端永远走自家后端；`agent/api.ts` 不出现任何外部域名 |
| Agent 误删 / 误提交 | 写操作工具在 UI 上二次确认：LLM 输出 tool_call 后前端弹「将执行：expense_submit(claim_id=42)，确认？」，确认后才真发 |
| 会话历史无限增长 | `agent_retention_days=90` cron 清理 + 单 session >200 messages 自动归档 |
| LLM 单次响应超长拖垮 SSE | `agent_stream_idle_timeout_seconds=60` 流式 idle 超时主动 abort |

---

## 18. 不做（明确 YAGNI）

- **不接 RAG / 向量检索**——发票/报销数据走 MCP 工具实时查，无需向量化
- **不接 Langfuse / OpenTelemetry**——`audit_logs` 已满足合规
- **不做多 profile 切换**——单 profile，按 user.scopes 过滤
- **不做多模型路由 / 路由调度**——单模型，按 `agent_llm_model` 配置
- **不做 WorkBuddy 集成**——MCP 端已开，第三方 client 自行接入；Web Agent 是另一个入口
- **不做会议纪要 / 文档生成**——超出本期范围
- **不做流式 idle 超时的二级清理**——靠 fastapi 进程回收即可

---

## 19. 待确认事项

无。本文档已覆盖所有架构决策点，等待用户最终审阅。