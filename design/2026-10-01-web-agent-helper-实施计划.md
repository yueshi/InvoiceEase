# Web Agent 助手实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给发票易 Web 管理后台内嵌一个自然语言 Agent 助手——用户用中文提问，Agent 自主挑选并调用 36 个 MCP 工具（嵌入式直调，非协议层），SSE 流式回传过程与结果。

**Architecture:** 移植 AuditMind-0928/auditmind/agent 的 R1 单循环架构（async generator loop + 事件流 + JSON 协议工具调用，兼容 Qwen 类模型）。后端新增 `invoicing/agent/` 包；工具层通过 SDK ContextVar 注入 Principal 后直接 `mcp.call_tool()`；前端 Vue3 全局抽屉 + 悬浮球。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x / SQLite(dev) · openai SDK（AsyncOpenAI，已依赖）· mcp SDK（已依赖）· Vue 3 / Pinia / ant-design-vue 4（不新增前端依赖）

**Spec:** `design/2026-10-01-web-agent-helper-design.md`（本计划由其推导；两文档同读）

## Global Constraints

- 不新增任何 Python / npm 依赖（openai、mcp 均已在 `backend/pyproject.toml`；前端仅用现有 antdv4/vue/pinia）。
- 离线部署硬约束：`offline_deploy=true` 时 LLM 必须走内网端点——Agent 复用 `settings.llm_base_url`，由既有 `ops/checks.py::check_offline_llm` 把关；**前端绝不直连任何 LLM 域名**，全部经 `/api/v1/agent/*` 后端代理。
- Agent 调用 MCP 工具一律通过 SDK 认证 ContextVar 注入（`auth_context_var`），构建自 `ROLE_DEFAULT_SCOPES[user.role]`——不得绕过 `@requires(scope)`。
- 审计：每次对话回合写 `audit_logs`（action=`AGENT_CHAT`, channel=`web`），含 user_id / session_id / tool_calls / tokens / error_code。
- 测试代码放 `test/`（pytest，`asyncio_mode=auto`，命令 `cd backend && uv run pytest ../test/xxx.py -v`）；前端测试 `web/src/**/__tests__/*.spec.ts`（vitest，`cd web && npm run test`）。
- 注释与提交信息用中文；提交信息遵循仓库风格（`feat:` / `fix:` / `docs:` 前缀）。
- 参考实现路径固定为 `/Users/james/AuditMind-0928/auditmind/agent/`（下称 `$AUDITMIND`）。

## 与设计的差异（有意的懒惰简化，逐条说明）

| 设计文档原文 | 本计划实现 | 理由 |
|---|---|---|
| 移植 `profile.py`（AgentProfile 抽象） | **不移植**——单 profile，`run_agent` 直接接收 prompt/tools | 一个 profile 的工厂模式是 YAGNI |
| `agent_messages.tool_results JSON` 列 | **不建该列**——只存 `tool_calls`（name/status/ms） | 工具输出不回放给 LLM，合规审计已有 tool 名单；后续需要再加列 |
| 前端 `AssistantMarkdown.vue` markdown 渲染 | **不渲染 markdown**——`white-space: pre-wrap` 纯文本 | 避免新增 markdown 依赖；MVP 够用 |
| `ContextChip.vue` 独立组件 | 折进 AgentDrawer 头部一个 `a-tag` | 一个 tag 不值得独立文件 |
| 写操作「前端弹确认框后才真发」 | **prompt 级确认**：system prompt 要求写操作先复述参数等用户回复「确认」；UI 弹框需要暂停/恢复协议，**延后** | SSE 单向流加确认协议成本高；对话轮本身就是天然确认机制 |
| 前端 axios `responseType:'stream'` | **fetch + ReadableStream** | axios 浏览器端不支持流式响应（node-only） |
| `agent_block_cloud_llm_domains` 新配置 | **不加**——复用既有 `check_offline_llm` | 它已覆盖「offline + 公网域名 → fail」 |
| SSE 6 类事件（含 `intent`/`proposal`） | **4 类**：`token`/`tool_call`/`done`/`error` | intent 是 AuditMind 意图分类器的遗留（已废弃）；proposal 本期无消费方 |
| `llm_enabled=false` → 503 + 前端 banner | **静态能力说明流**（分片 token） | 复用移植的 fallback 机制，比 503 对用户更友好，且前端零特殊分支 |
| 36 工具「高频 12 个进 prompt」 | **全 36 个进 manifest**（`build_tools_manifest` 自动生成） | 按 scope 过滤需要机器可读的 tool→scope 映射（不存在）；懒披露延后 |

---

## 任务总览

| # | 任务 | 依赖 | 交付物 |
|---|---|---|---|
| 1 | 数据模型 + 迁移 + config 字段 | — | agent_sessions/agent_messages 表；agent_* 配置 |
| 2 | core 移植层（events/tools/context/guards/json_extract） | — | `invoicing/agent/core/` 骨架 |
| 3 | LLM 客户端 + OpenAIDriver | 2 | 流式/非流式 driver + 探测解析 |
| 4 | Agent Loop 移植 | 2,3 | `run_agent_loop` + WRAPUP 收尾轮 |
| 5 | SSE 层 + schemas | 2 | emitter + bridge + ChatRequest/SessionOut |
| 6 | MCP 工具桥 | 2 | `build_tools_for_user` 嵌入式直调 |
| 7 | history + observability | 1,2 | 历史加载 + 回合落库/审计 |
| 8 | system_prompts + main_agent | 3,4,5 | `run_agent()` 入口 |
| 9 | API 路由 | 5,6,7,8 | `/api/v1/agent/*` + SSE |
| 10 | 启动自检 + 保留 cron | 1,9 | check_agent_llm + agent_retention |
| 11 | 前端 types + api + store | 9 | SSE 消费 + 状态机 |
| 12 | 前端组件 + App.vue 挂载 | 11 | 悬浮球 + 抽屉 + 消息流 |
| 13 | 会话侧栏 + 上下文注入 | 12 | 会话切换/新建/删除 + 页面上下文 |
| 14 | 端到端冒烟 + 使用文档 | 全部 | 手工验收 + docs/ HTML |

---

### Task 1: 数据模型 + Alembic 迁移 + config 字段

**Files:**
- Create: `backend/src/invoicing/models/agent.py`
- Modify: `backend/src/invoicing/models/__init__.py`
- Create: `backend/alembic/versions/c2d3e4f5a6b7_agent_会话与消息.py`
- Modify: `backend/src/invoicing/config.py`（末尾追加 agent 段）
- Test: `test/test_agent_models.py`

**Interfaces:**
- Produces: `AgentSession(user_id, tenant_id, title, created_at, updated_at, archived_at)`；`AgentMessage(session_id, role, content, tool_calls, duration_ms, input_tokens, output_tokens, created_at)`；ORM 类名 `AgentSession` / `AgentMessage` 从 `invoicing.models` 导出。config 字段：`agent_enabled` / `agent_max_steps` / `agent_max_context_turns` / `agent_max_context_tokens` / `agent_llm_model` / `agent_llm_temperature` / `agent_max_tokens` / `agent_session_retention_days`。

- [ ] **Step 1: 写模型文件**

```python
# backend/src/invoicing/models/agent.py
"""Web Agent 助手：会话与消息（design/2026-10-01-web-agent-helper-design.md）。

与 audit_logs 解耦：本表管「会话回放」，审计管「合规追溯」——同一个回合
两边都写（见 agent/observability.py record_turn）。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from invoicing.db import Base
from invoicing.models.fields import utcnow


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow,
        onupdate=func.now(), nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 本轮 tool_call 统计：[{"tool": str, "status": "done"|"failed", "ms": int}]
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
```

- [ ] **Step 2: 导出 + config**

`models/__init__.py` 两处修改：import 段加 `from invoicing.models.agent import AgentMessage, AgentSession`；`__all__` 加 `"AgentMessage", "AgentSession"`（保持字母序插入）。

`config.py` 在 `auto_review_threshold` 行后追加：

```python
    # Web Agent 助手（design/2026-10-01-web-agent-helper-design.md）
    agent_enabled: bool = True            # false 时 /api/v1/agent/* 全 404
    agent_max_steps: int = 8              # loop 最大轮数
    agent_max_context_turns: int = 6      # 历史带入轮数上限
    agent_max_context_tokens: int = 8000  # 历史 token 上限（估算）
    agent_llm_model: str = ""             # 空 = 复用 llm_model_text
    agent_llm_temperature: float = 0.3
    agent_max_tokens: int = 2000          # 单轮输出上限（给推理模型留空间）
    agent_session_retention_days: int = 90  # 会话保留天数（cron 清理）
```

- [ ] **Step 3: 迁移文件**

```python
# backend/alembic/versions/c2d3e4f5a6b7_agent_会话与消息.py
"""agent_sessions / agent_messages Web Agent 助手

Revision ID: c2d3e4f5a6b7
Revises: b8c9d0e1f2a3
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_messages_session_id", "agent_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_messages_session_id", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_sessions_user_id", table_name="agent_sessions")
    op.drop_table("agent_sessions")
```

先验证 revision id 无冲突：`cd backend && grep -rn "c2d3e4f5a6b7" alembic/versions/ | grep -v agent` 应无输出。

- [ ] **Step 4: 写测试**

```python
# test/test_agent_models.py
"""Agent 会话/消息模型：ORM 可用 + CASCADE 级联删除。"""
from invoicing.models import AgentMessage, AgentSession, User


def test_session_and_message_roundtrip(db):
    user = User(username="agent_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id, title="测试会话")
    db.add(s)
    db.commit()
    db.add(AgentMessage(session_id=s.id, role="user", content="你好"))
    db.add(AgentMessage(session_id=s.id, role="assistant", content="你好，我是发票易助手",
                        tool_calls=[{"tool": "invoice_list", "status": "done", "ms": 12}]))
    db.commit()

    assert db.query(AgentMessage).filter_by(session_id=s.id).count() == 2
    assert db.query(AgentSession).filter_by(id=s.id).one().title == "测试会话"


def test_cascade_delete(db):
    user = User(username="agent_u2", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()
    db.add(AgentMessage(session_id=s.id, role="user", content="x"))
    db.commit()
    db.delete(s)
    db.commit()
    assert db.query(AgentMessage).filter_by(session_id=s.id).count() == 0
```

注意：CASCADE 依赖 SQLite 的 `PRAGMA foreign_keys=ON`——已在 `db.py:17-25` 全局开启，无需处理。

- [ ] **Step 5: 跑测试 + 迁移验证**

```bash
cd backend
uv run pytest ../test/test_agent_models.py -v          # 期望 2 passed
uv run alembic upgrade head                             # 期望无错误
uv run alembic downgrade -1 && uv run alembic upgrade head  # 回滚往返验证
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/models/agent.py backend/src/invoicing/models/__init__.py \
        backend/src/invoicing/config.py backend/alembic/versions/c2d3e4f5a6b7_agent_会话与消息.py \
        test/test_agent_models.py
git commit -m "feat(agent): agent_sessions/agent_messages 表 + agent_* 配置项"
```

---

### Task 2: core 移植层（events / tools / context / guards / json_extract）

**Files:**
- Create: `backend/src/invoicing/agent/__init__.py`（空文件）
- Create: `backend/src/invoicing/agent/core/__init__.py`（空文件）
- Create: `backend/src/invoicing/agent/core/events.py`（从 $AUDITMIND 拷贝）
- Create: `backend/src/invoicing/agent/core/tools.py`（拷贝）
- Create: `backend/src/invoicing/agent/core/context.py`（拷贝）
- Create: `backend/src/invoicing/agent/core/guards.py`（拷贝）
- Create: `backend/src/invoicing/agent/json_extract.py`（拷贝）
- Test: `test/test_agent_core.py`

**Interfaces:**
- Produces（后续任务依赖的精确名字）：
  - `events.py`: `AgentStartEvent / TurnStartEvent / MessageStartEvent / MessageUpdateEvent / ToolStartEvent / ToolUpdateEvent / ToolEndEvent / AgentEndEvent`（每个有 `.to_dict()`）
  - `tools.py`: `AgentTool`(Protocol) / `AgentToolResult.text(text, is_error=False, **meta)` / `TextContent` / `ToolUpdateCallback`
  - `context.py`: `ToolCall(id, name, args)` / `UserMessage(content)` / `AssistantMessage(text, tool_calls, thinking, usage)` / `ToolResultMessage(tool_call_id, content, is_error)` / `AgentContext(system_prompt, messages, tools, max_turns, metadata)` / `AgentState(ctx)`
  - `guards.py`: `capability_guard(state) -> bool`
  - `json_extract.py`: `extract_json_dict(text) -> dict | None`

- [ ] **Step 1: 拷贝源文件**

```bash
SRC=/Users/james/AuditMind-0928/auditmind/agent
DST=/Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent
mkdir -p $DST/core
touch $DST/__init__.py $DST/core/__init__.py
cp $SRC/core/events.py $DST/core/events.py
cp $SRC/core/tools.py $DST/core/tools.py
cp $SRC/core/context.py $DST/core/context.py
cp $SRC/core/guards.py $DST/core/guards.py
cp /Users/james/AuditMind-0928/auditmind/core/json_extract.py $DST/json_extract.py
```

- [ ] **Step 2: 导入路径替换（macOS `sed -i ''`；Linux 用 `sed -i`）**

```bash
cd /Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent
sed -i '' \
  -e 's/from auditmind\.agent\./from invoicing.agent./g' \
  -e 's/from auditmind\.core\.json_extract/from invoicing.agent.json_extract/g' \
  core/*.py
sed -i '' 's/AUDITMIND_AGENT_LOOP_GUARD/INVOICING_AGENT_LOOP_GUARD/g' core/guards.py
# 校验无残留 import（docstring 里提及 auditmind 字样可以留）
grep -rn "^from auditmind\|^import auditmind" core/ && echo "还有残留！" || echo "OK"
```

- [ ] **Step 3: 写测试**

```python
# test/test_agent_core.py
"""agent core 移植层：事件序列化 / 消息家族 / guard 规则 / JSON 容错提取。"""
import asyncio
from invoicing.agent.core.context import (
    AgentContext, AgentState, AssistantMessage, ToolCall, ToolResultMessage, UserMessage,
)
from invoicing.agent.core.events import AgentEndEvent, ToolEndEvent
from invoicing.agent.core.guards import capability_guard
from invoicing.agent.core.tools import AgentToolResult, TextContent
from invoicing.agent.json_extract import extract_json_dict


def test_event_to_dict():
    ev = ToolEndEvent(turn_index=1, tool_call_id="tc-1", tool_name="invoice_list",
                      output="ok", is_error=False, duration_ms=42)
    d = ev.to_dict()
    assert d["type"] == "tool_end" and d["tool_name"] == "invoice_list" and d["duration_ms"] == 42
    end = AgentEndEvent(turn_index=1, reason="no_more_tool_calls")
    assert end.to_dict()["reason"] == "no_more_tool_calls"


def test_message_family():
    m = AssistantMessage(text="hi", tool_calls=[ToolCall(id="1", name="t", args={})])
    assert m.role == "assistant" and m.tool_calls[0].name == "t"
    r = ToolResultMessage(tool_call_id="1", content=[TextContent(text="out")])
    assert r.role == "tool_result" and r.content[0].text == "out"


def test_extract_json_dict_prefers_outer():
    text = '前缀废话 {"tool_call": [{"name": "invoice_list", "args": {}}]} 后缀'
    data = extract_json_dict(text)
    assert data is not None and data["tool_call"][0]["name"] == "invoice_list"
    assert extract_json_dict("纯文本没有 JSON") is None


def test_guard_r1_consecutive_fail():
    """R1：同一工具连续 2 次失败 → 停，并写 stop_reason_detail。"""
    ctx = AgentContext()
    state = AgentState(ctx=ctx)
    ctx.messages.append(AssistantMessage(text="", tool_calls=[ToolCall(id="a", name="x", args={})]))
    ctx.messages.append(ToolResultMessage(tool_call_id="a", content=[TextContent(text="err")], is_error=True))
    ctx.messages.append(AssistantMessage(text="", tool_calls=[ToolCall(id="b", name="x", args={})]))
    ctx.messages.append(ToolResultMessage(tool_call_id="b", content=[TextContent(text="err2")], is_error=True))
    assert capability_guard(state) is True
    assert state.stop_reason_detail == "consecutive_fail:x"


def test_guard_r3_hallucinated_tool_call():
    """R3：文本里写 tool_call JSON 但没真发 tool_calls → 幻觉，停。"""
    ctx = AgentContext()
    state = AgentState(ctx=ctx)
    ctx.messages.append(AssistantMessage(text='我来查：{"tool_call": [{"name": "invoice_list"}]}'))
    assert capability_guard(state) is True
    assert state.stop_reason_detail == "hallucinated_tool_call_json"
```

（`asyncio` import 若 lint 提示未用可删。）

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_core.py -v   # 期望 5 passed
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/agent/ test/test_agent_core.py
git commit -m "feat(agent): 移植 core 类型层（events/tools/context/guards/json_extract）"
```

---

### Task 3: LLM 客户端 + OpenAIDriver

**Files:**
- Create: `backend/src/invoicing/agent/llm.py`
- Create: `backend/src/invoicing/agent/core/driver.py`（先拷贝再替换 driver 类）
- Test: `test/test_agent_driver.py`

**Interfaces:**
- Consumes: `invoicing.agent.core.context.{AssistantMessage, Message, ToolCall}`（Task 2）、`invoicing.agent.json_extract.extract_json_dict`（Task 2）
- Produces:
  - `invoicing.agent.llm`: `get_agent_client()` → `AsyncOpenAI | None`；`agent_model()` → `str`；`estimate_tokens(text) -> int`
  - `driver.py`: `OpenAIDriver(client, model, temperature=0.3, max_tokens=2000)`；`.chat(system_prompt, messages, tools) -> AssistantMessage`；`.stream_chat(...) -> AsyncGenerator[tuple[str, str | AssistantMessage], None]`（`("text_delta", str)` / `("assistant_message", AssistantMessage)`）；模块级 `TOOL_CALL_INSTRUCTIONS`、`build_tools_manifest(tools)`

- [ ] **Step 1: llm.py**

```python
# backend/src/invoicing/agent/llm.py
"""Agent LLM 客户端：与 parse/llm.py 共用同一套 INVOICING_LLM_* 配置。

用 AsyncOpenAI（真异步流式）——同步 client 在 async def 里迭代会阻塞
event loop，SSE token 会堆积到流末一次性冲出（AuditMind 已踩过）。
"""
from invoicing.config import settings

_client = None
_tried = False


def get_agent_client():
    """惰性单例 AsyncOpenAI；llm_enabled=False → None（run_agent 走静态兜底）。"""
    global _client, _tried
    if _client is None and not _tried:
        if settings.llm_enabled:
            from openai import AsyncOpenAI

            _client = AsyncOpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
        _tried = True
    return _client


def agent_model() -> str:
    """Agent 用对话模型：agent_llm_model 为空时复用 llm_model_text。"""
    return settings.agent_llm_model or settings.llm_model_text


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（历史裁剪用，非精确）.

    中文按 1.5 字符/token；英文按 4 字符/token；混合按中文比例线性插值。
    """
    if not text:
        return 0
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4 + 0.999)  # 向上取整
```

- [ ] **Step 2: driver.py 拷贝 + 导入替换**

```bash
SRC=/Users/james/AuditMind-0928/auditmind/agent
DST=/Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent
cp $SRC/core/driver.py $DST/core/driver.py
cd $DST
sed -i '' \
  -e 's/from auditmind\.agent\./from invoicing.agent./g' \
  -e 's/from auditmind\.core\.json_extract/from invoicing.agent.json_extract/g' \
  core/driver.py
```

- [ ] **Step 3: 替换 driver 类**

把 `core/driver.py` 中从 `class LiteLLMDriver:` 起、到 `# ============ 内部工具函数 ============` 之前的一整段，替换为：

```python
class OpenAIDriver:
    """基于 openai AsyncOpenAI 的 driver（流式 + 非流式）.

    工具调用协议与流式探测沿用 AuditMind 版（_probe_stream_buffer 等），
    只把底层 chat_client 换成 openai SDK 原生异步客户端。
    """

    def __init__(self, client, model: str, temperature: float = 0.3, max_tokens: int = 2000):
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list,
    ) -> AssistantMessage:
        """非流式一次拿全 content——收尾轮 / unit test / 非实时场景。"""
        oa_messages = _build_openai_messages(system_prompt, messages, tools)
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=oa_messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            print(f"[agent-driver] LLM 调用失败: {type(e).__name__}: {e}", file=sys.stderr)
            raise

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            print("[agent-driver] LLM 返回空 content", file=sys.stderr)
            return AssistantMessage(text=_EMPTY_REPLY_TEXT)

        usage = getattr(resp, "usage", None)
        msg = _parse_assistant_reply(content)
        msg.usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }
        return msg

    async def stream_chat(
        self,
        system_prompt: str,
        messages: list[Message],
        tools: list,
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式——边收边判别 tool_call vs final（判别逻辑同 AuditMind）。

        判别后：
          - tool_call 路径：收完一次性解析（全程不 yield，防前导话术泄漏给前端）
          - final 路径：先把缓冲整块 yield，之后逐 chunk yield
        流末 yield ("assistant_message", AssistantMessage)（含 usage）。
        """
        oa_messages = _build_openai_messages(system_prompt, messages, tools)

        buffer = ""
        mode: str | None = None  # None=探测中 | "tool_call" | "final"
        full_content = ""
        usage: dict = {}

        try:
            # ponytail: stream_options.include_usage 依赖 provider 支持；
            # 不支持的端点（如个别 Ollama 版）可去掉此参数，usage 记为 0，不影响主流程
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=oa_messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    usage = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                    }
                choices = getattr(chunk, "choices", None) or []
                delta = ""
                if choices and choices[0].delta is not None:
                    delta = choices[0].delta.content or ""
                if not delta:
                    continue
                full_content += delta

                if mode is None:
                    buffer += delta
                    verdict = _probe_stream_buffer(buffer.lstrip())
                    if verdict is True:
                        mode = "tool_call"
                    elif verdict is False:
                        mode = "final"
                        yield ("text_delta", buffer)
                        buffer = ""
                    # None → 继续攒 buffer
                elif mode == "final":
                    yield ("text_delta", delta)
                # mode == "tool_call" 时不 yield，等收完解析
        except Exception as e:
            print(f"[agent-driver] LLM 流式调用失败: {type(e).__name__}: {e}", file=sys.stderr)
            raise

        # 流已结束但还没判定（超短回复）
        if mode is None:
            if _probe_stream_buffer(buffer.lstrip()) is True:
                mode = "tool_call"
            else:
                mode = "final"
                if buffer:
                    yield ("text_delta", buffer)

        if mode == "tool_call":
            msg = _parse_assistant_reply(full_content)
            if not msg.tool_calls and full_content.strip():
                # 以为是 tool_call 但解析失败 → 退化为 final，别让用户看不到输出
                print(
                    f"[agent-driver] tool_call 路径解析失败，退化为 final text（预览：{full_content[:200]!r}）",
                    file=sys.stderr,
                )
                yield ("text_delta", full_content)
                msg = AssistantMessage(text=full_content)
        else:
            msg = AssistantMessage(text=full_content)

        # 空响应兜底：整条流一个字符都没有（网关吞输出）
        if not full_content.strip() and not msg.tool_calls:
            print("[agent-driver] LLM 返回空 content（流式）", file=sys.stderr)
            yield ("text_delta", _EMPTY_REPLY_TEXT)
            msg = AssistantMessage(text=_EMPTY_REPLY_TEXT)

        msg.usage = usage
        yield ("assistant_message", msg)
```

同时把文件末尾 `__all__` 的 `"LiteLLMDriver"` 改为 `"OpenAIDriver"`，并在 `StreamChunk` 定义处保持不变（docstring 提到 pi/AuditMind 字样可留）。

- [ ] **Step 4: 写测试**

```python
# test/test_agent_driver.py
"""OpenAIDriver：流式探测（tool_call vs final）、usage、空响应兜底、非流式。"""
import pytest
from invoicing.agent.core.driver import OpenAIDriver, _probe_stream_buffer
from invoicing.agent.core.context import AssistantMessage


class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens = p
        self.completion_tokens = c


class _Msg:
    def __init__(self, content):
        self.content = content


class _Delta:
    def __init__(self, content):
        self.delta = _Msg(content)


class _Chunk:
    def __init__(self, content=None, usage=None):
        self.choices = [_Delta(content)] if content is not None else []
        self.usage = usage


class _NonStreamResp:
    def __init__(self, content, usage=None):
        class _Choice:
            def __init__(self, m): self.message = m
        self.choices = [_Choice(_Msg(content))]
        self.usage = usage


class _FakeCompletions:
    def __init__(self, chunks=None, content=None, usage=None):
        self._chunks = chunks or []
        self._content = content
        self._usage = usage
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            async def _gen():
                for c in self._chunks:
                    yield c
            return _gen()
        return _NonStreamResp(self._content, self._usage)


class _FakeClient:
    def __init__(self, **kw):
        self.chat = type("C", (), {"completions": _FakeCompletions(**kw)})()


async def _collect(driver, **kw):
    out = []
    async for item in driver.stream_chat(system_prompt="sys", messages=[], tools=kw.get("tools", [])):
        out.append(item)
    return out


async def test_final_text_streams_deltas():
    client = _FakeClient(chunks=[_Chunk("你好，"), _Chunk("这是回答"), _Chunk(usage=_Usage(5, 7))])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    deltas = [p for k, p in out if k == "text_delta"]
    assert "".join(deltas) == "你好，这是回答"
    msg = out[-1][1]
    assert isinstance(msg, AssistantMessage) and msg.text == "你好，这是回答"
    assert msg.usage == {"prompt_tokens": 5, "completion_tokens": 7}


async def test_bare_json_is_tool_call_no_text_leak():
    payload = '{"tool_call": [{"name": "invoice_list", "args": {"page": 1}}]}'
    client = _FakeClient(chunks=[_Chunk(payload)])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    deltas = [p for k, p in out if k == "text_delta"]
    assert deltas == []  # tool_call 路径不泄漏 JSON 给前端
    msg = out[-1][1]
    assert msg.tool_calls[0].name == "invoice_list" and msg.tool_calls[0].args == {"page": 1}


async def test_nl_preamble_then_json_still_tool_call():
    """Qwen 常在 JSON 前垫一句自然语言——必须仍判 tool_call（否则工具从不执行）。"""
    client = _FakeClient(chunks=[_Chunk("我先查一下。\n"), _Chunk('{"tool_call": [{"name": "invoice_list", "args": {}}]}')])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    assert [p for k, p in out if k == "text_delta"] == []
    assert out[-1][1].tool_calls[0].name == "invoice_list"


async def test_empty_stream_fallback_text():
    client = _FakeClient(chunks=[])
    driver = OpenAIDriver(client, model="m")
    out = await _collect(driver)
    assert out[-1][1].text == "（LLM 无输出）"


async def test_non_stream_chat_parses_usage():
    client = _FakeClient(content="直接回答", usage=_Usage(3, 4))
    driver = OpenAIDriver(client, model="m")
    msg = await driver.chat(system_prompt="sys", messages=[], tools=[])
    assert msg.text == "直接回答" and msg.usage["prompt_tokens"] == 3


def test_probe_buffer_rules():
    assert _probe_stream_buffer("{") is True
    assert _probe_stream_buffer("```json\n{}") is True
    assert _probe_stream_buffer("```chart-pie\n{}") is False
    assert _probe_stream_buffer("普通短文") is None          # 未到判定阈值
    assert _probe_stream_buffer("长" * 300) is False          # 超过 _NL_FINAL_CHARS → final
```

- [ ] **Step 5: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_driver.py -v   # 期望 6 passed
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/agent/llm.py backend/src/invoicing/agent/core/driver.py test/test_agent_driver.py
git commit -m "feat(agent): AsyncOpenAI 客户端 + OpenAIDriver（流式探测/JSON 协议）"
```

---

### Task 4: Agent Loop 移植

**Files:**
- Create: `backend/src/invoicing/agent/core/loop.py`（从 $AUDITMIND 拷贝 + 导入替换）
- Test: `test/test_agent_loop.py`

**Interfaces:**
- Consumes: Task 2 全部类型；`LLMDriver` Protocol（driver.py 内，Task 3）
- Produces: `run_agent_loop(driver, state, session_id, cancel_event=None, on_turn_start=None, on_turn_end=None, *, final_answer_turn=True) -> AsyncGenerator[AgentEvent, None]`；`WRAPUP_INSTRUCTION`；`WRAPUP_FALLBACK_TEXT`

- [ ] **Step 1: 拷贝 + 替换**

```bash
cp /Users/james/AuditMind-0928/auditmind/agent/core/loop.py \
   /Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent/core/loop.py
cd /Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent
sed -i '' 's/from auditmind\.agent\./from invoicing.agent./g' core/loop.py
grep -n "auditmind" core/loop.py | grep "^.*import" || echo "OK：无残留 import"
```

loop.py 逻辑**零改动**（它已处理：max_turns / cancel / WRAPUP 收尾轮 / 工具并行执行 / 单工具异常不影响其它 / guard 早检时序）。这是移植中最值钱的一块，不要"优化"。

- [ ] **Step 2: 写测试**

```python
# test/test_agent_loop.py
"""run_agent_loop：终止条件 / 工具执行 / 异常隔离 / WRAPUP 收尾轮。"""
import asyncio
from invoicing.agent.core.context import (
    AgentContext, AgentState, AssistantMessage, ToolCall,
)
from invoicing.agent.core.events import AgentEndEvent, ToolEndEvent
from invoicing.agent.core.loop import run_agent_loop
from invoicing.agent.core.tools import AgentToolResult


class FakeDriver:
    """按脚本逐轮吐 assistant_message（只实现 stream_chat，loop 优先走它）。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.last_tools = None

    async def stream_chat(self, system_prompt, messages, tools):
        self.calls += 1
        self.last_tools = tools
        reply = self.replies.pop(0)
        if reply.text:
            yield ("text_delta", reply.text)
        yield ("assistant_message", reply)


class EchoTool:
    name = "echo"
    description = "回显"
    schema = {"type": "object"}

    async def execute(self, tool_call_id, args, cancel_event, update_callback):
        return AgentToolResult.text(f"echo:{args.get('x')}")


class BoomTool:
    name = "boom"
    description = "总是抛错"
    schema = {"type": "object"}

    async def execute(self, tool_call_id, args, cancel_event, update_callback):
        raise RuntimeError("炸了")


def _state(tools, max_turns=8):
    ctx = AgentContext(tools=tools, max_turns=max_turns)
    return AgentState(ctx=ctx)


async def _run(driver, state):
    events = []
    async for ev in run_agent_loop(driver, state, session_id="t1"):
        events.append(ev)
    return events


async def test_plain_final_stops():
    driver = FakeDriver([AssistantMessage(text="直接回答")])
    state = _state([])
    events = await _run(driver, state)
    assert isinstance(events[-1], AgentEndEvent)
    assert events[-1].reason == "no_more_tool_calls"
    assert driver.calls == 1


async def test_tool_call_executes_then_final():
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="echo", args={"x": 1})]),
        AssistantMessage(text="完成"),
    ])
    state = _state([EchoTool()])
    events = await _run(driver, state)
    tool_ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert len(tool_ends) == 1 and tool_ends[0].output == "echo:1" and not tool_ends[0].is_error
    assert events[-1].reason == "no_more_tool_calls"
    # 工具结果以 ToolResultMessage 入历史
    from invoicing.agent.core.context import ToolResultMessage
    assert any(isinstance(m, ToolResultMessage) and m.content[0].text == "echo:1"
               for m in state.ctx.messages)


async def test_unknown_tool_is_error_result():
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="nope", args={})]),
        AssistantMessage(text="好吧"),
    ])
    state = _state([EchoTool()])
    events = await _run(driver, state)
    tool_ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert tool_ends[0].is_error and "未知工具" in tool_ends[0].output


async def test_tool_exception_isolated():
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="boom", args={})]),
        AssistantMessage(text="炸了但我继续"),
    ])
    state = _state([BoomTool()])
    events = await _run(driver, state)
    tool_ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert tool_ends[0].is_error and "RuntimeError" in tool_ends[0].output
    assert events[-1].reason == "no_more_tool_calls"


async def test_max_turns_triggers_wrapup():
    """跑满轮数且从未产出可见 final → 补一轮 tools=[] 逼出结论。"""
    driver = FakeDriver([
        AssistantMessage(text="", tool_calls=[ToolCall(id="tc1", name="echo", args={"x": 1})]),
        AssistantMessage(text="收尾结论"),
    ])
    state = _state([EchoTool()], max_turns=1)
    events = await _run(driver, state)
    assert driver.calls == 2
    assert driver.last_tools == []            # 收尾轮工具已关闭
    assert events[-1].reason == "max_turns"   # stop_reason 不被收尾轮改写


async def test_cancel_stops():
    driver = FakeDriver([AssistantMessage(text="x"), AssistantMessage(text="y")])
    state = _state([])
    cancel = asyncio.Event()
    cancel.set()
    events = []
    async for ev in run_agent_loop(driver, state, session_id="t", cancel_event=cancel):
        events.append(ev)
    assert events[-1].reason == "cancelled"
    assert driver.calls == 0
```

- [ ] **Step 3: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_loop.py -v   # 期望 6 passed
```

若 `test_plain_final_stops` 断言 `driver.calls == 1` 失败，检查 FakeDriver 是否同时定义了 `chat`——loop 优先 `stream_chat`，不要加 `chat` 方法。

- [ ] **Step 4: Commit**

```bash
git add backend/src/invoicing/agent/core/loop.py test/test_agent_loop.py
git commit -m "feat(agent): 移植 run_agent_loop（含 WRAPUP 收尾轮与异常隔离测试）"
```

---

### Task 5: SSE 层 + sse_bridge + schemas

**Files:**
- Create: `backend/src/invoicing/agent/sse.py`
- Create: `backend/src/invoicing/agent/core/sse_bridge.py`（拷贝 + 替换）
- Create: `backend/src/invoicing/schemas/agent.py`
- Test: `test/test_agent_sse.py`

**Interfaces:**
- Consumes: Task 2 events/context
- Produces:
  - `AgentSSEEmitter(max_queue_size=200)`：`.emit(event_type, data)`（type 限定 `{"token","tool_call","done","error"}`，越界抛 ValueError）；`.close()`；`.stream() -> AsyncGenerator[str, None]`（帧格式 `data: {json}\n\n`，溢出丢最旧）
  - `bridge_events_to_sse(events, emitter, tool_calls_stats) -> AgentEndEvent | None`；`last_assistant_text(messages) -> str`
  - `schemas/agent.py`：`AgentContext(page="Global", invoice_id=None, claim_id=None, receipt_id=None, month=None)`；`ChatRequest(message: str(1..4000), context: AgentContext)`；`SessionOut(id, title, created_at, updated_at)`（from_attributes）；`MessageOut(id, role, content, tool_calls, duration_ms, input_tokens, output_tokens, created_at)`（from_attributes）

- [ ] **Step 1: sse.py**

```python
# backend/src/invoicing/agent/sse.py
"""Agent 专用 SSE 事件流（per-request emitter）。

帧契约：``data: {json}\\n\\n`` 裸帧（无具名 event 行——EventSource onmessage
对具名事件不触发）；溢出丢最旧（token 流宁丢旧不丢新）。
机制移植自 auditmind/core/sse.py 的 SSEChannel（只取 agent 需要的子集，
不移植 SSEHub/heartbeat——它们服务 pipeline/recon，与本场景无关）。
"""
import asyncio
import json
from typing import AsyncGenerator

EVENT_TYPES = {"token", "tool_call", "done", "error"}

_CHANNEL_END = object()


def format_sse_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class AgentSSEEmitter:
    """单请求 SSE 事件发射器（emit 同步写队列；stream 异步消费）。"""

    def __init__(self, max_queue_size: int = 200):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._closed = False

    def emit(self, event_type: str, data: dict) -> None:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {event_type}")
        if self._closed:
            return
        event = {"type": event_type, "data": data}
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:  # 丢最旧保最新
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass

    def close(self) -> None:
        """结束流：入队 sentinel 让 stream 优雅退出（队满时挤掉最旧重试，sentinel 必须落位）。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(_CHANNEL_END)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(_CHANNEL_END)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def stream(self) -> AsyncGenerator[str, None]:
        while True:
            item = await self._queue.get()
            if item is _CHANNEL_END:
                return
            yield format_sse_frame(item)
```

- [ ] **Step 2: sse_bridge 拷贝 + 替换**

```bash
cp /Users/james/AuditMind-0928/auditmind/agent/core/sse_bridge.py \
   /Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent/core/sse_bridge.py
cd /Users/james/ai_workspace/InvoiceEase/backend/src/invoicing/agent
sed -i '' 's/from auditmind\.agent\./from invoicing.agent./g' core/sse_bridge.py
```

- [ ] **Step 3: schemas/agent.py**

```python
# backend/src/invoicing/schemas/agent.py
"""Web Agent 助手请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentContext(BaseModel):
    """前端页面上下文（帮助 LLM 理解「这个/这条」指什么）。"""

    page: str = Field(default="Global", description="当前路由 path，如 /invoices")
    invoice_id: int | None = None
    claim_id: int | None = None      # 报销单 id
    receipt_id: int | None = None
    month: str | None = None         # YYYY-MM


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: AgentContext = Field(default_factory=AgentContext)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str | None
    tool_calls: list | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
```

- [ ] **Step 4: 写测试**

```python
# test/test_agent_sse.py
"""SSE emitter 帧契约 + 事件桥折叠。"""
import pytest
from invoicing.agent.core.context import AssistantMessage
from invoicing.agent.core.events import (
    AgentEndEvent, MessageUpdateEvent, ToolEndEvent, ToolStartEvent,
)
from invoicing.agent.core.sse_bridge import bridge_events_to_sse, last_assistant_text
from invoicing.agent.sse import AgentSSEEmitter


async def test_emitter_frames_roundtrip():
    em = AgentSSEEmitter()
    em.emit("token", {"text": "你好"})
    em.emit("tool_call", {"tool": "invoice_list", "status": "start"})
    em.close()
    frames = [f async for f in em.stream()]
    assert frames[0] == 'data: {"type": "token", "data": {"text": "你好"}}\n\n'
    assert '"tool_call"' in frames[1]
    assert len(frames) == 2


def test_emitter_rejects_unknown_type():
    em = AgentSSEEmitter()
    with pytest.raises(ValueError):
        em.emit("intent", {})


async def test_bridge_folds_events():
    em = AgentSSEEmitter()
    stats: list[dict] = []

    async def _events():
        yield MessageUpdateEvent(turn_index=0, kind="text_delta", delta="你")
        yield MessageUpdateEvent(turn_index=0, kind="thinking_delta", delta="想")  # 不透出
        yield ToolStartEvent(turn_index=0, tool_call_id="1", tool_name="invoice_list", args={})
        yield ToolEndEvent(turn_index=0, tool_call_id="1", tool_name="invoice_list",
                           output="ok", is_error=False, duration_ms=8)
        yield AgentEndEvent(turn_index=0, reason="no_more_tool_calls")

    end = await bridge_events_to_sse(_events(), em, stats)
    em.close()
    frames = [f async for f in em.stream()]
    assert len(frames) == 3  # token + tool_start + tool_end（thinking 与内部事件不透出）
    assert end is not None and end.reason == "no_more_tool_calls"
    assert stats == [{"tool": "invoice_list", "status": "done", "ms": 8}]


def test_last_assistant_text_skips_tool_turns():
    msgs = [
        AssistantMessage(text="", tool_calls=[]),
        AssistantMessage(text="中间态"),
        AssistantMessage(text="最终回答"),
    ]
    assert last_assistant_text(msgs) == "最终回答"
```

- [ ] **Step 5: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_sse.py -v   # 期望 4 passed
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/agent/sse.py backend/src/invoicing/agent/core/sse_bridge.py \
        backend/src/invoicing/schemas/agent.py test/test_agent_sse.py
git commit -m "feat(agent): SSE emitter + 事件桥 + agent schemas"
```

---

### Task 6: MCP 工具桥（嵌入式直调）

**Files:**
- Create: `backend/src/invoicing/agent/tools_bridge.py`
- Test: `test/test_agent_tools_bridge.py`

**Interfaces:**
- Consumes: `invoicing.mcp.server.mcp`（MCPServer 单例）、`invoicing.mcp.identity.ROLE_DEFAULT_SCOPES`、Task 2 `AgentToolResult`
- Produces: `build_tools_for_user(user) -> Awaitable[list[McpToolAdapter]]`（async）；`McpToolAdapter`（属性 `.name/.description/.schema`，方法 `async execute(tool_call_id, args, cancel_event, update_callback) -> AgentToolResult`）

**机制**（已验证）：`mcp.call_tool(name, args)` → tool_manager → 同步工具函数经 `anyio.to_thread.run_sync` 执行（线程池，不阻塞事件循环），contextvars 跨线程传播；因此先 `auth_context_var.set(AuthenticatedUser(AccessToken(...)))` 再调用即可让 `@requires(scope)` 读到注入身份。工具体异常会被 SDK 包成 `ToolError` 重抛 → 适配器捕获转为 `is_error=True` 结果。

- [ ] **Step 1: 实现 tools_bridge.py**

```python
# backend/src/invoicing/agent/tools_bridge.py
"""把 36 个 MCP 工具包成 AgentTool（嵌入式直调，非 MCP 协议层）。

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
            return AgentToolResult.text(_result_to_text(result), is_error=bool(getattr(result, "isError", False)))
        except Exception as e:
            # SDK 把工具体异常包成 ToolError（如 ScopeDenied / ValueError 直译）
            return AgentToolResult.text(f"[tool_exception] {type(e).__name__}: {e}", is_error=True)
        finally:
            auth_context_var.reset(token)


async def build_tools_for_user(user) -> list[McpToolAdapter]:
    """当前用户可用工具全集（36 个；scope 守门在工具层 @requires）。"""
    from invoicing.mcp.server import mcp

    infos = await mcp.list_tools()
    return [
        McpToolAdapter(info, user_id=user.id, username=user.username, role=user.role)
        for info in infos
    ]
```

- [ ] **Step 2: 写测试**

```python
# test/test_agent_tools_bridge.py
"""嵌入式工具桥：真实走 mcp.call_tool + 身份注入 + scope 拒绝。"""
import asyncio

from invoicing.agent.tools_bridge import build_tools_for_user
from invoicing.models import User


async def _noop(_delta: str) -> None:
    return None


async def test_build_and_call_list_invoices(db):
    user = User(username="bridge_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()

    tools = await build_tools_for_user(user)
    names = [t.name for t in tools]
    assert "invoice_list" in names and "invoice_delete" in names  # 全集都在 manifest

    t = next(t for t in tools if t.name == "invoice_list")
    res = await t.execute("tc-1", {"page": 1, "page_size": 5}, asyncio.Event(), _noop)
    assert res.is_error is False
    assert "total" in res.content[0].text or "items" in res.content[0].text  # 空库也是合法 JSON


async def test_scope_denied_for_employee(db):
    """employee 调 invoice_delete（invoice:admin）→ is_error + 权限字样。"""
    user = User(username="bridge_u2", password_hash="x", role="employee")
    db.add(user)
    db.commit()

    tools = await build_tools_for_user(user)
    t = next(t for t in tools if t.name == "invoice_delete")
    res = await t.execute("tc-2", {"invoice_id": 999}, asyncio.Event(), _noop)
    assert res.is_error is True
    assert "权限" in res.content[0].text


async def test_unknown_args_wrapped_as_error(db):
    """参数不合法（缺 invoice_id）→ SDK 校验异常 → is_error 结果，不炸 loop。"""
    user = User(username="bridge_u3", password_hash="x", role="admin")
    db.add(user)
    db.commit()

    tools = await build_tools_for_user(user)
    t = next(t for t in tools if t.name == "invoice_detail")
    res = await t.execute("tc-3", {}, asyncio.Event(), _noop)
    assert res.is_error is True
```

注意：这三个测试直接 import `invoicing.mcp.server`（模块级 `mcp = build_server()`），无需 HTTP 服务。测试的 `db` fixture 建表包含 agent 表（Task 1）。

- [ ] **Step 3: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_tools_bridge.py -v   # 期望 3 passed
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/invoicing/agent/tools_bridge.py test/test_agent_tools_bridge.py
git commit -m "feat(agent): MCP 工具桥（嵌入式直调 + 用户身份注入 + scope 守门）"
```

---

### Task 7: history + observability

**Files:**
- Create: `backend/src/invoicing/agent/history.py`
- Create: `backend/src/invoicing/agent/observability.py`
- Modify: `backend/src/invoicing/audit.py`（classify_outcome 加 AGENT_CHAT 分支）
- Modify: `backend/src/invoicing/models/enums.py`（AuditAction 加 AGENT_CHAT）
- Test: `test/test_agent_history_obs.py`

**Interfaces:**
- Consumes: Task 1 模型、Task 2 消息家族、`invoicing.audit.write_audit`
- Produces:
  - `load_session_history(db, session_id, max_turns=6, max_tokens=8000) -> list[Message]`
  - `record_turn(db, *, session_id, user_id, user_message, assistant_reply, tool_calls, input_tokens, output_tokens, duration_ms, error_code) -> None`（含会话标题首轮自动生成 + audit_logs 写入）

- [ ] **Step 1: history.py**

```python
# backend/src/invoicing/agent/history.py
"""会话历史加载：agent_messages 表 → Loop messages。

裁剪策略（移植 AuditMind load_session_history）：
- 先限轮数（一轮 = 1 条 user + 1 条 assistant）
- 再按 token 估算从最老一轮整轮丢，不切开一轮
- assistant 空文本（纯工具调用中间态）不算有效轮
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from invoicing.agent.core.context import AssistantMessage, Message, UserMessage
from invoicing.agent.llm import estimate_tokens
from invoicing.models import AgentMessage


def load_session_history(
    db: Session,
    session_id: int,
    max_turns: int = 6,
    max_tokens: int = 8000,
) -> list[Message]:
    if max_turns <= 0:
        return []

    rows = db.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id,
               AgentMessage.role.in_(("user", "assistant")))
        .order_by(AgentMessage.id.desc())
        .limit(max_turns * 2)
    ).scalars().all()

    # 倒序取 → 正序组"轮"：assistant 必须紧跟其 user 且文本非空
    turns: list[tuple[str, str]] = []
    pending_user: str | None = None
    for row in reversed(rows):
        if row.role == "user":
            pending_user = row.content or ""
        elif pending_user is not None and (row.content or "").strip():
            turns.append((pending_user, row.content))
            pending_user = None

    def _token_sum(ts: list[tuple[str, str]]) -> int:
        return sum(estimate_tokens(u) + estimate_tokens(a) for u, a in ts)

    while turns and _token_sum(turns) > max_tokens:
        turns.pop(0)

    messages: list[Message] = []
    for user_text, assistant_text in turns:
        messages.append(UserMessage(content=user_text))
        messages.append(AssistantMessage(text=assistant_text))
    return messages
```

- [ ] **Step 2: observability.py**

```python
# backend/src/invoicing/agent/observability.py
"""Agent 回合落库：agent_messages（会话回放）+ audit_logs（合规审计）。"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from invoicing.audit import write_audit
from invoicing.models import AgentMessage, AgentSession


def record_turn(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    user_message: str,
    assistant_reply: str,
    tool_calls: list[dict],
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    error_code: str | None,
) -> None:
    session = db.get(AgentSession, session_id)
    if session is None:
        return
    if not session.title:
        session.title = (user_message or "").strip()[:40] or "新会话"
    # 显式碰 updated_at：session 行其余字段没变时 onupdate 不会触发，
    # 而侧栏排序依赖它（SQLAlchemy onupdate 仅在本行有 UPDATE 时生效）
    session.updated_at = datetime.now(timezone.utc)

    db.add(AgentMessage(session_id=session_id, role="user", content=user_message))
    db.add(AgentMessage(
        session_id=session_id, role="assistant", content=assistant_reply or "",
        tool_calls=tool_calls, duration_ms=duration_ms,
        input_tokens=input_tokens, output_tokens=output_tokens,
    ))
    write_audit(
        db, action="AGENT_CHAT", user_id=user_id, channel="web",
        detail={
            "session_id": session_id,
            "tool_calls": [t.get("tool") for t in tool_calls],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": duration_ms,
            "error_code": error_code,
        },
    )
    db.commit()
```

- [ ] **Step 3: audit.py / enums.py 小改**

`models/enums.py` 的 `AuditAction` 加成员：`AGENT_CHAT = "AGENT_CHAT"`。

`audit.py` 的 `classify_outcome` 在 `if action == "CONFIG_CHANGE":` 之前插入：

```python
    if action == "AGENT_CHAT":
        return "error" if (detail or {}).get("error_code") else "success"
```

- [ ] **Step 4: 写测试**

```python
# test/test_agent_history_obs.py
"""历史加载（配对/截断）+ 回合落库（消息 + 审计 + 首轮标题）。"""
from invoicing.agent.history import load_session_history
from invoicing.agent.observability import record_turn
from invoicing.models import AgentMessage, AgentSession, AuditLog, User


def _mk_session(db, n_pairs: int):
    user = User(username="hist_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()
    for i in range(n_pairs):
        db.add(AgentMessage(session_id=s.id, role="user", content=f"问题{i}"))
        db.add(AgentMessage(session_id=s.id, role="assistant", content=f"回答{i}"))
    db.commit()
    return user, s


def test_history_pairs_and_orders(db):
    _, s = _mk_session(db, 3)
    msgs = load_session_history(db, s.id, max_turns=10, max_tokens=100000)
    assert [m.role for m in msgs] == ["user", "assistant"] * 3
    assert msgs[0].content == "问题0" and msgs[-1].content == "回答2"


def test_history_truncates_by_turns(db):
    _, s = _mk_session(db, 5)
    msgs = load_session_history(db, s.id, max_turns=2, max_tokens=100000)
    assert len(msgs) == 4
    assert msgs[-1].content == "回答4"   # 保留最近两轮


def test_history_skips_empty_assistant(db):
    user, s = _mk_session(db, 1)
    db.add(AgentMessage(session_id=s.id, role="assistant", content=""))  # 中间态空文本
    db.add(AgentMessage(session_id=s.id, role="user", content="问题X"))
    db.add(AgentMessage(session_id=s.id, role="assistant", content="回答X"))
    db.commit()
    msgs = load_session_history(db, s.id, max_turns=10)
    assert [m.content for m in msgs] == ["问题0", "回答0", "问题X", "回答X"]


def test_record_turn_sets_title_and_audit(db):
    user, s = _mk_session(db, 0)
    record_turn(db, session_id=s.id, user_id=user.id, user_message="帮我查本月的发票",
                assistant_reply="本月共 3 张", tool_calls=[{"tool": "invoice_list", "status": "done", "ms": 9}],
                input_tokens=10, output_tokens=20, duration_ms=1500, error_code=None)
    db.expire_all()
    assert db.get(AgentSession, s.id).title == "帮我查本月的发票"
    msgs = db.query(AgentMessage).filter_by(session_id=s.id).all()
    assert len(msgs) == 2 and msgs[1].tool_calls[0]["tool"] == "invoice_list"
    log = db.query(AuditLog).filter_by(action="AGENT_CHAT").one()
    assert log.channel == "web" and log.outcome == "success"
    assert log.detail["session_id"] == s.id
```

- [ ] **Step 5: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_history_obs.py -v   # 期望 4 passed
uv run pytest ../test/test_audit.py -v                              # 确认 classify_outcome 未回归
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/agent/history.py backend/src/invoicing/agent/observability.py \
        backend/src/invoicing/audit.py backend/src/invoicing/models/enums.py \
        test/test_agent_history_obs.py
git commit -m "feat(agent): 历史加载 + 回合落库（agent_messages + AGENT_CHAT 审计）"
```

---

### Task 8: system_prompts + main_agent

**Files:**
- Create: `backend/src/invoicing/agent/system_prompts.py`
- Create: `backend/src/invoicing/agent/main_agent.py`
- Test: 无独立测试（由 Task 9 集成测试覆盖）

**Interfaces:**
- Consumes: Task 3 `OpenAIDriver`/`agent_model`、Task 4 `run_agent_loop`、Task 5 `AgentSSEEmitter`/`bridge_events_to_sse`/`last_assistant_text`/`AgentContext`、`settings.agent_*`
- Produces: `run_agent(*, message, context, emitter, history, tools, session_id, client, cancel_event=None) -> dict`（返回 `{"tool_calls", "input_tokens", "output_tokens", "duration_ms", "error_code", "assistant_reply"}`）；`DEFAULT_SYSTEM_PROMPT`

- [ ] **Step 1: system_prompts.py**

```python
# backend/src/invoicing/agent/system_prompts.py
"""发票易 Web 助手 system prompt。

工具清单与调用协议由 driver 的 build_tools_manifest + TOOL_CALL_INSTRUCTIONS
在运行时自动追加，这里只写业务规则。
"""

DEFAULT_SYSTEM_PROMPT = """你是「发票易」（InvoiceEase）企业发票管理平台的 Web 助手。用户可能是员工、财务专员或财务主管，会问发票查询、报销建单、回单对账、报表统计等问题。你要根据用户问题与页面上下文，自主决定调用工具还是直接回答。

【工具调用协议】（必须严格遵守，否则工具不会被执行）
- **要调工具**：只输出 JSON ``{"tool_call": [{"name": "工具名", "args": {…}}]}``（可包在 ```json 里），前后不要写任何自然语言。
- **要给最终答案**：直接写中文，开头不要以 `{` 或 ``` 起手。工具调用与最终答案分轮次发，不要混在同一条回复里。

【写操作必须先确认】（提交/删除/审批类操作）
下列工具属于写操作，调用前必须先用纯文本向用户复述「将要执行的操作 + 关键参数」，并等用户回复确认后才能调用：
  expense_submit（提交报销单）、expense_approve（审批）、invoice_delete（删除发票）、invoice_unblock（放行拦截票）、invoice_update（修改发票字段）、red_invoice_link（补关联红字票）、bank_account_delete / company_info_delete（删除主数据）。
其余新增/导入/查询类工具可直接调用，不需要确认。

【停止规则】（结构性兜底，违反会被强制停）
- 同一工具连续 2 次失败 / 同一工具累计 ≥3 次失败 / 工具总错误率 ≥50%（至少 2 次调用）→ 停
- 在回复里写 ``{"tool_call":…}`` JSON 但没有真正调通 → 视为幻觉，停

【调查预算】
工具调用最多 {max_steps} 轮（一轮可并行发多个）。能并发的调用并在一起发；越接近上限越要收敛，用已有信息作答。

【怎么选工具】（标注的工具名以可用工具清单为准）
- 问"某张/某批发票" → invoice_list（按状态/日期/关键词筛）、invoice_detail（看单张详情）
- 问"本月花了多少/成本构成" → invoice_report；问"这个月整体情况/异常" → invoice_health_report
- 建报销：expense_create（建草稿单）→ expense_add_entry（建事项）→ expense_add_invoices（按发票号加票，票要在 expense_eligible_invoices 池里）→ expense_submit（提交，需确认）
- 问"哪些票还能报" → expense_eligible_invoices；问"报销单进度" → expense_list
- 回单相关：receipt_list（清单）、receipt_report（对账/无票费用）、receipt_pair（手动配对）
- 发票复核：invoice_ai_review（生成预判）、invoice_classify（费用归类）
- 上传本地文件：用户只给了服务器路径时用 invoice_ingest / receipt_ingest / sales_invoice_import；没有文件路径就先问用户
- 与发票报销无关的闲聊/常识 → 不调工具，直接答；完全无关时礼貌引导回业务

【每轮只回答当前这一问】（反重复）
- 判断"现在该答哪一问"只看最后一条 [用户消息]，历史是背景不是题目。
- 不要复述/重打上一轮答复；用户明确要求"再讲一遍/展开"才重述，且要重新组织措辞。
- 说任何字段值（金额/状态/号码）之前，必须有**本轮工具返回**的数据支撑；没查过就说"我去查一下"，不要编。

【写作风格】
- 语气自然像和同事解释；金额、发票号码、日期要精确引用工具返回的原文。
- 数据缺失时直接说"这块信息看不到/工具没返回"，不硬凑。
- 列表、表格可以适度用 markdown（前端按纯文本渲染，保持简单）。
"""


def build_system_prompt(max_steps: int) -> str:
    return DEFAULT_SYSTEM_PROMPT.replace("{max_steps}", str(max_steps))
```

- [ ] **Step 2: main_agent.py**

```python
# backend/src/invoicing/agent/main_agent.py
"""MainAgent：发票易 Web 助手统一入口（移植 AuditMind R1 重构版）。

职责：组装 Loop 上下文 → 跑 run_agent_loop（经 SSE 桥转前端事件）→ 返回观测统计。
不落库（落库由 API 层用独立 session 调 observability.record_turn）。
"""
import time

from invoicing.agent.core.context import AgentContext as LoopCtx, AgentState, UserMessage
from invoicing.agent.core.driver import OpenAIDriver
from invoicing.agent.core.guards import capability_guard
from invoicing.agent.core.loop import run_agent_loop
from invoicing.agent.core.sse_bridge import bridge_events_to_sse, last_assistant_text
from invoicing.agent.llm import agent_model
from invoicing.agent.system_prompts import build_system_prompt
from invoicing.config import settings
from invoicing.schemas.agent import AgentContext

_STATIC_FALLBACK = """我可以帮你做这些事（当前未启用大模型，以下为功能说明）：

1. **发票查询**：告诉我时间范围/状态/关键词，例如"列出本月待复核的发票"
2. **报销单**：从可报销票池选票建单、加票、提交审批
3. **银行回单**：查回单清单、对账汇报、手动配对
4. **报表**：月度成本报表、健康报告

启用大模型（INVOICING_LLM_ENABLED=true + API Key）后我就能直接执行这些操作。
"""


async def run_agent(
    *,
    message: str,
    context: AgentContext,
    emitter,
    history,
    tools,
    session_id: str,
    client,
    cancel_event=None,
) -> dict:
    """跑主 Agent，返回观测统计（shape 供 record_turn 使用）。"""
    started = time.perf_counter()

    # LLM 不可用 → 静态能力说明（分片发，保留打字机体感）
    if client is None:
        for i in range(0, len(_STATIC_FALLBACK), 20):
            emitter.emit("token", {"text": _STATIC_FALLBACK[i:i + 20]})
        emitter.emit("done", {"total_ms": 0, "tool_count": 0})
        return {
            "tool_calls": [], "input_tokens": 0, "output_tokens": 0,
            "duration_ms": 0, "error_code": None, "assistant_reply": _STATIC_FALLBACK,
        }

    # 组装 loop 上下文（历史在前，当前轮带 [用户消息]/[页面上下文] 包装）
    user_content = _format_user_message(message, context)
    loop_ctx = LoopCtx(
        system_prompt=build_system_prompt(settings.agent_max_steps),
        messages=[*history, UserMessage(content=user_content)],
        tools=list(tools),
        max_turns=settings.agent_max_steps,
        metadata={"page": context.page},
    )
    state = AgentState(ctx=loop_ctx)

    driver = OpenAIDriver(
        client, model=agent_model(),
        temperature=settings.agent_llm_temperature,
        max_tokens=settings.agent_max_tokens,
    )
    tool_calls_stats: list[dict] = []
    error_code: str | None = None

    try:
        await bridge_events_to_sse(
            run_agent_loop(
                driver, state, session_id=session_id,
                cancel_event=cancel_event, on_turn_end=capability_guard,
            ),
            emitter, tool_calls_stats,
        )
    except Exception as e:
        error_code = "AGENT_EXCEPTION"
        emitter.emit("error", {"code": error_code, "message": str(e), "retryable": True})

    total_ms = int((time.perf_counter() - started) * 1000)
    emitter.emit("done", {
        "total_ms": total_ms,
        "tool_count": len(tool_calls_stats),
        "stop_reason_detail": state.stop_reason_detail,
    })

    history_len = len(history)
    return {
        "tool_calls": tool_calls_stats,
        "input_tokens": state.total_input_tokens,
        "output_tokens": state.total_output_tokens,
        "duration_ms": total_ms,
        "error_code": error_code,
        # 只在本轮消息里找 final（切片跳过历史）——否则空回复时会回捞上一轮答复当本轮结果
        "assistant_reply": last_assistant_text(loop_ctx.messages[history_len:]),
    }


def _format_user_message(message: str, context: AgentContext) -> str:
    """用户原话 + 页面上下文打包。

    `[用户消息]` 前缀是 system prompt「只看最后一条 [用户消息]」判据的唯一锚点：
    历史轮走裸文本（DB 存的就是原文），只有当前轮带包装。
    """
    lines = [f"[用户消息]\n{message}\n"]
    bits = [f"page={context.page}"]
    if context.invoice_id:
        bits.append(f"invoice_id={context.invoice_id}")
    if context.claim_id:
        bits.append(f"claim_id={context.claim_id}")
    if context.receipt_id:
        bits.append(f"receipt_id={context.receipt_id}")
    if context.month:
        bits.append(f"month={context.month}")
    lines.append(f"\n[页面上下文]\n{', '.join(bits)}")
    return "\n".join(lines)
```

- [ ] **Step 3: 导入冒烟**

```bash
cd backend && uv run python -c "
from invoicing.agent.main_agent import run_agent, _format_user_message
from invoicing.schemas.agent import AgentContext
print(_format_user_message('你好', AgentContext(page='/invoices', invoice_id=7)))
"
```
期望打印两行 `[用户消息]…` / `[页面上下文] page=/invoices, invoice_id=7`。

- [ ] **Step 4: Commit**

```bash
git add backend/src/invoicing/agent/system_prompts.py backend/src/invoicing/agent/main_agent.py
git commit -m "feat(agent): system prompt（发票场景/写操作确认）+ run_agent 入口"
```

---

### Task 9: API 路由 + 开关接入

**Files:**
- Create: `backend/src/invoicing/api/agent.py`
- Modify: `backend/src/invoicing/api/__init__.py`
- Test: `test/test_agent_api.py`

**Interfaces:**
- Consumes: 全部后端任务
- Produces（HTTP 契约，前端 Task 11 依赖）：
  - `GET /api/v1/agent/sessions` → `SessionOut[]`（本人，未归档，按 updated_at 倒序）
  - `POST /api/v1/agent/sessions` → `SessionOut`（body 可空）
  - `DELETE /api/v1/agent/sessions/{id}` → `{"ok": true}`（软删：置 archived_at）
  - `GET /api/v1/agent/sessions/{id}/messages` → `MessageOut[]`
  - `POST /api/v1/agent/sessions/{id}/messages`（body `ChatRequest`）→ `text/event-stream`，帧 `data: {"type": "token"|"tool_call"|"done"|"error", "data": {...}}\n\n`
  - 全部 404 当 `agent_enabled=false`；会话非本人 → 404

- [ ] **Step 1: api/agent.py**

```python
# backend/src/invoicing/api/agent.py
"""Web Agent 助手 API：会话 CRUD + SSE 流式对话。

设计见 design/2026-10-01-web-agent-helper-design.md。
- 鉴权：get_current_user（JWT + SUSPENDED + 强制改密闸门全套生效）
- 落库：流结束后后台任务用**独立 session**（请求 session 在流式响应期间生命周期不可靠）
- 断连：响应生成器被取消时置 cancel_event，loop 在下一轮检查点停止
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from invoicing.agent.history import load_session_history
from invoicing.agent.llm import get_agent_client
from invoicing.agent.main_agent import run_agent
from invoicing.agent.observability import record_turn
from invoicing.agent.sse import AgentSSEEmitter
from invoicing.agent.tools_bridge import build_tools_for_user
from invoicing.config import settings
from invoicing.db import SessionLocal, get_db
from invoicing.models import AgentMessage, AgentSession, User
from invoicing.schemas.agent import ChatRequest, MessageOut, SessionOut
from invoicing.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _gate() -> None:
    if not settings.agent_enabled:
        raise HTTPException(404, "智能助手未启用")


def _own_session(db: Session, user: User, session_id: int, *, include_archived: bool = False) -> AgentSession:
    s = db.get(AgentSession, session_id)
    if s is None or s.user_id != user.id or (not include_archived and s.archived_at is not None):
        raise HTTPException(404, "会话不存在")
    return s


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    return (
        db.query(AgentSession)
        .filter(AgentSession.user_id == user.id, AgentSession.archived_at.is_(None))
        .order_by(AgentSession.updated_at.desc())
        .limit(100)
        .all()
    )


@router.post("/sessions", response_model=SessionOut)
def create_session(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    s = AgentSession(user_id=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/sessions/{session_id}")
def archive_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    from datetime import datetime, timezone

    s = _own_session(db, user, session_id)
    s.archived_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _gate()
    _own_session(db, user, session_id)
    return (
        db.query(AgentMessage)
        .filter(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.asc())
        .limit(500)
        .all()
    )


@router.post("/sessions/{session_id}/messages")
async def chat(
    session_id: int,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _gate()
    session = _own_session(db, user, session_id)
    history = load_session_history(
        db, session.id,
        max_turns=settings.agent_max_context_turns,
        max_tokens=settings.agent_max_context_tokens,
    )
    tools = await build_tools_for_user(user)
    client = get_agent_client()

    emitter = AgentSSEEmitter()
    cancel_event = asyncio.Event()
    stats: dict = {
        "tool_calls": [], "input_tokens": 0, "output_tokens": 0,
        "duration_ms": 0, "error_code": None, "assistant_reply": "",
    }

    async def _run_and_record() -> None:
        try:
            stats.update(await run_agent(
                message=body.message, context=body.context, emitter=emitter,
                history=history, tools=tools, session_id=str(session.id),
                client=client, cancel_event=cancel_event,
            ))
        except Exception:
            logger.exception("agent run 失败")
            emitter.emit("error", {"code": "AGENT_INTERNAL", "message": "助手内部错误", "retryable": True})
            emitter.emit("done", {"total_ms": 0, "tool_count": 0})
        finally:
            try:
                with SessionLocal() as db2:  # 独立 session：请求依赖的 session 在流式期间生命周期不可靠
                    record_turn(
                        db2, session_id=session.id, user_id=user.id,
                        user_message=body.message,
                        assistant_reply=stats.get("assistant_reply") or "",
                        tool_calls=stats.get("tool_calls") or [],
                        input_tokens=stats.get("input_tokens") or 0,
                        output_tokens=stats.get("output_tokens") or 0,
                        duration_ms=stats.get("duration_ms") or 0,
                        error_code=stats.get("error_code"),
                    )
            except Exception:
                logger.exception("agent 回合落库失败")
            emitter.close()

    asyncio.create_task(_run_and_record())

    async def _frames():
        try:
            async for frame in emitter.stream():
                yield frame
        finally:
            # 客户端断开（关抽屉/刷新）→ 通知 loop 下一轮检查点停止
            cancel_event.set()
            emitter.close()

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: 注册路由**

`api/__init__.py`：import 列表加 `agent`（保持字母序在最前）；`api_router.include_router(agent.router)` 放在 auth 之前（保持 include 顺序集中，不强制字母序——追加到 `include_router(auth.router)` 之前即可）。

- [ ] **Step 3: 写测试**

```python
# test/test_agent_api.py
"""Agent API：会话 CRUD 鉴权 + SSE 流式（假 LLM）+ 审计落库。"""
import time

import pytest
from fastapi.testclient import TestClient

from invoicing.main import create_app
from invoicing.models import AuditLog


@pytest.fixture()
def client(db):
    with TestClient(create_app()) as c:
        yield c


def _login(client, username="admin", password="admin123") -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_sessions_crud_and_ownership(client, db):
    h = _login(client)
    r = client.post("/api/v1/agent/sessions", headers=h)
    assert r.status_code == 200
    sid = r.json()["id"]
    assert client.get("/api/v1/agent/sessions", headers=h).json()[0]["id"] == sid

    # 另一个用户看不到/删不掉别人的会话（会话归属隔离）
    from invoicing.models import User
    from invoicing.security import hash_password
    other = User(username="other_u", password_hash=hash_password("pass1234"), role="employee")
    db.add(other)
    db.commit()
    h2 = _login(client, "other_u", "pass1234")
    assert client.get(f"/api/v1/agent/sessions/{sid}/messages", headers=h2).status_code == 404
    assert client.delete(f"/api/v1/agent/sessions/{sid}", headers=h2).status_code == 404


def test_chat_streams_with_fake_llm(client, db, monkeypatch):
    h = _login(client)
    sid = client.post("/api/v1/agent/sessions", headers=h).json()["id"]

    # 假 AsyncOpenAI：create(stream=True) 返回只吐一段 final 文本的异步迭代器
    class _Delta:
        def __init__(self, c): self.delta = type("M", (), {"content": c})()

    class _Chunk:
        def __init__(self, c): self.choices = [_Delta(c)]; self.usage = None

    class _Completions:
        async def create(self, **kw):
            assert kw.get("stream") is True
            async def _gen():
                yield _Chunk("你好")
                yield _Chunk("，我是助手")
            return _gen()

    fake = type("F", (), {"chat": type("C", (), {"completions": _Completions()})()})()

    import invoicing.api.agent as agent_api
    monkeypatch.setattr(agent_api, "get_agent_client", lambda: fake)

    with client.stream("POST", f"/api/v1/agent/sessions/{sid}/messages",
                       json={"message": "你好", "context": {"page": "/"}}, headers=h) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert '"type": "token"' in body and '"type": "done"' in body
    assert "我是助手" in body

    # 等后台落库任务完成（轮询审计行出现，上限 3s）
    deadline = time.time() + 3
    log = None
    while time.time() < deadline:
        db.expire_all()
        log = db.query(AuditLog).filter_by(action="AGENT_CHAT").first()
        if log:
            break
        time.sleep(0.1)
    assert log is not None and log.channel == "web"


def test_chat_llm_disabled_falls_back_static(client, db, monkeypatch):
    """llm_enabled=false（conftest 默认）→ 静态说明，不报错。"""
    h = _login(client)
    sid = client.post("/api/v1/agent/sessions", headers=h).json()["id"]
    import invoicing.api.agent as agent_api
    monkeypatch.setattr(agent_api, "get_agent_client", lambda: None)
    with client.stream("POST", f"/api/v1/agent/sessions/{sid}/messages",
                       json={"message": "你能做什么", "context": {"page": "/"}}, headers=h) as r:
        body = "".join(r.iter_text())
    assert '"type": "token"' in body and "发票查询" in body
```

注意：`_login` 用的默认管理员由 lifespan 的 `ensure_admin_user` 创建（conftest 已把 `INVOICING_ADMIN_PASSWORD` 设为 `admin123`、hash cost 降到 4）。若 `test_api_mcp_tokens.py` 里有现成的 `admin_headers` 类 fixture，优先复用之（保持仓库测试风格一致）。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_api.py -v   # 期望 3 passed
```

- [ ] **Step 5: 回归**

```bash
cd backend && uv run pytest ../test -v -x -q   # 全量回归；新增路由不应 impact 既有用例
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/invoicing/api/agent.py backend/src/invoicing/api/__init__.py test/test_agent_api.py
git commit -m "feat(agent): /api/v1/agent 路由（会话 CRUD + SSE 流式对话 + 审计落库）"
```

---

### Task 10: 启动自检 + 会话保留 cron

**Files:**
- Modify: `backend/src/invoicing/ops/checks.py`
- Modify: `backend/src/invoicing/scheduler.py`
- Test: `test/test_agent_ops.py`

**Interfaces:**
- Consumes: `settings.agent_*`、`AgentSession` 模型
- Produces: 自检项 `agent_llm`（warn 级）；`_purge_expired_agent_sessions(db, retention_days=None) -> int`；cron 任务 `agent_retention`（每日 03:47）

- [ ] **Step 1: checks.py 加自检项**

在 `check_offline_llm` 之后新增：

```python
def check_agent_llm() -> tuple[str, str]:
    """Web Agent 助手开启但 LLM 未启用 → 助手只能显示静态说明（warn）。"""
    if not settings.agent_enabled:
        return "info", "agent_enabled 未启用"
    if not settings.llm_enabled:
        return "warn", "agent_enabled=true 但 llm_enabled=false：助手对话将只显示静态功能说明"
    return "ok", "Web Agent 助手：LLM 已启用"
```

`_CHECKS` 列表追加：`("agent_llm", check_agent_llm),`（放在 `("offline_llm", check_offline_llm)` 之后）。

离线闸门无需新增——`check_offline_llm` 检查的是 `settings.llm_base_url`，Agent 与其共用同一端点，已被覆盖。

- [ ] **Step 2: scheduler.py 加保留清理**

在 `_scheduled_audit_retention` 之后新增（复用其结构）：

```python
def _purge_expired_agent_sessions(db, retention_days: int | None = None) -> int:
    """清理过期 Agent 会话（消息经 ON DELETE CASCADE 一并删除），返回删除条数。"""
    from datetime import datetime, timedelta, timezone

    from invoicing.models import AgentSession

    days = retention_days if retention_days is not None else settings.agent_session_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = db.query(AgentSession).filter(AgentSession.updated_at < cutoff)
    count = q.count()
    if count:
        q.delete(synchronize_session=False)
        db.commit()
    return count


def _scheduled_agent_retention() -> None:
    with SessionLocal() as db:
        try:
            removed = _purge_expired_agent_sessions(db)
            if removed:
                logger.info("Agent 会话保留期清理：删除 %s 个会话", removed)
        except Exception:
            logger.exception("Agent 会话保留期清理失败")
```

注册（紧跟 audit_retention 的 register 之后）：

```python
# 每日 03:47（避开整点与既有任务 02:17/03:17）
register_task("agent_retention", _scheduled_agent_retention, trigger="cron", hour=3, minute=47)
```

- [ ] **Step 3: 写测试**

```python
# test/test_agent_ops.py
"""Agent 运维：自检项存在 + 会话保留清理。"""
from datetime import datetime, timedelta, timezone

from invoicing.models import AgentMessage, AgentSession, User
from invoicing.ops.checks import run_all_checks


def test_check_agent_llm_present():
    names = [c["name"] for c in run_all_checks()]
    assert "agent_llm" in names


def test_purge_expired_sessions(db):
    from invoicing.scheduler import _purge_expired_agent_sessions

    user = User(username="purge_u", password_hash="x", role="employee")
    db.add(user)
    db.commit()
    old = AgentSession(user_id=user.id, updated_at=datetime.now(timezone.utc) - timedelta(days=200))
    fresh = AgentSession(user_id=user.id)
    db.add_all([old, fresh])
    db.commit()
    db.add(AgentMessage(session_id=old.id, role="user", content="旧"))
    db.commit()

    removed = _purge_expired_agent_sessions(db, retention_days=90)
    assert removed == 1
    assert db.get(AgentSession, old.id) is None
    assert db.query(AgentMessage).filter_by(session_id=old.id).count() == 0  # CASCADE
    assert db.get(AgentSession, fresh.id) is not None
```

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run pytest ../test/test_agent_ops.py ../test/test_api_ops.py -v   # 新增 2 passed + 回归
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/invoicing/ops/checks.py backend/src/invoicing/scheduler.py test/test_agent_ops.py
git commit -m "feat(agent): 启动自检 agent_llm + 会话保留清理 cron（03:47）"
```

---

### Task 11: 前端 types + api + Pinia store

**Files:**
- Create: `web/src/agent/types.ts`
- Create: `web/src/agent/api.ts`
- Create: `web/src/agent/store.ts`
- Test: `web/src/agent/__tests__/store.spec.ts`

**Interfaces:**
- Consumes: 后端 HTTP 契约（Task 9）
- Produces:
  - `types.ts`: `SSEEvent`、`AgentSession`、`AgentMessage`、`AgentToolCall`、`ChatContext`
  - `api.ts`: `listSessions()` / `createSession()` / `deleteSession(id)` / `listMessages(id)` / `streamMessage(sessionId, body, onEvent, signal)`（fetch + ReadableStream）
  - `store.ts`: `useAgentStore`（state: `drawerOpen/sessions/currentSessionId/messages/streamingText/streamingTools/streaming/error/abort`；actions: `openDrawer/closeDrawer/loadSessions/createSession/switchSession/ensureSession/sendMessage/cancel/reset`）

- [ ] **Step 1: types.ts**

```ts
// web/src/agent/types.ts
export interface SSEEvent {
  type: "token" | "tool_call" | "done" | "error";
  data: {
    text?: string;
    tool?: string;
    status?: "start" | "done" | "failed";
    ms?: number;
    code?: string;
    message?: string;
    [k: string]: unknown;
  };
}

export interface AgentSession {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentToolCall {
  tool: string;
  status: "start" | "done" | "failed";
  ms?: number;
}

export interface AgentMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  tool_calls: AgentToolCall[] | null;
  duration_ms?: number | null;
  created_at: string;
}

export interface ChatContext {
  page: string;
  invoice_id?: number | null;
  claim_id?: number | null;
  receipt_id?: number | null;
  month?: string | null;
}
```

- [ ] **Step 2: api.ts**

```ts
// web/src/agent/api.ts
// Agent HTTP 客户端。SSE 用 fetch + ReadableStream（axios 浏览器端不支持流式响应）。
import { api, TOKEN_KEY } from "../api/client";
import type { AgentMessage, AgentSession, ChatContext, SSEEvent } from "./types";

export async function listSessions(): Promise<AgentSession[]> {
  return (await api.get<AgentSession[]>("/agent/sessions")).data;
}

export async function createSession(): Promise<AgentSession> {
  return (await api.post<AgentSession>("/agent/sessions")).data;
}

export async function deleteSession(id: number): Promise<void> {
  await api.delete(`/agent/sessions/${id}`);
}

export async function listMessages(id: number): Promise<AgentMessage[]> {
  return (await api.get<AgentMessage[]>(`/agent/sessions/${id}/messages`)).data;
}

/** POST 一条消息并逐帧消费 SSE；signal.abort() 可中途取消。 */
export async function streamMessage(
  sessionId: number,
  body: { message: string; context: ChatContext },
  onEvent: (ev: SSEEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY);
  const resp = await fetch(`/api/v1/agent/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败（HTTP ${resp.status}）`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(5).trim()) as SSEEvent);
      } catch {
        // 空帧/非 JSON 帧跳过
      }
    }
  }
}
```

- [ ] **Step 3: store.ts**

```ts
// web/src/agent/store.ts
// Agent 会话 store：消息列表 + 流式状态机（token 合并 / tool_call start→done|failed）。
import { defineStore } from "pinia";
import * as api from "./api";
import type { AgentMessage, AgentSession, AgentToolCall, ChatContext, SSEEvent } from "./types";

export const useAgentStore = defineStore("agent", {
  state: () => ({
    drawerOpen: false,
    sessions: [] as AgentSession[],
    currentSessionId: null as number | null,
    messages: [] as AgentMessage[],
    streamingText: "",
    streamingTools: [] as AgentToolCall[],
    streaming: false,
    error: null as { code: string; message: string } | null,
    abort: null as AbortController | null,
  }),
  actions: {
    openDrawer() {
      this.drawerOpen = true;
      void this.ensureSession();
    },
    closeDrawer() {
      this.drawerOpen = false;
    },
    /** 登出/切换身份时清空（App.vue 调用）。 */
    reset() {
      this.cancel();
      this.drawerOpen = false;
      this.sessions = [];
      this.currentSessionId = null;
      this.messages = [];
      this.streamingText = "";
      this.streamingTools = [];
      this.error = null;
    },
    async loadSessions() {
      try {
        this.sessions = await api.listSessions();
      } catch {
        /* 抽屉打开失败不弹窗，静默（401 由 axios 拦截器统一跳登录） */
      }
    },
    async createSession() {
      const s = await api.createSession();
      this.sessions.unshift(s);
      this.currentSessionId = s.id;
      this.messages = [];
      this.error = null;
    },
    async switchSession(id: number) {
      this.currentSessionId = id;
      this.error = null;
      this.messages = await api.listMessages(id);
    },
    async removeSession(id: number) {
      await api.deleteSession(id);
      this.sessions = this.sessions.filter((s) => s.id !== id);
      if (this.currentSessionId === id) {
        this.currentSessionId = null;
        this.messages = [];
        await this.ensureSession();
      }
    },
    async ensureSession() {
      if (!this.sessions.length) await this.loadSessions();
      if (this.currentSessionId) return;
      if (this.sessions.length) await this.switchSession(this.sessions[0].id);
      else await this.createSession();
    },
    async sendMessage(text: string, context: ChatContext) {
      if (!this.currentSessionId || this.streaming) return;
      this.messages.push({
        id: -Date.now(), role: "user", content: text, created_at: "", tool_calls: null,
      });
      this.streamingText = "";
      this.streamingTools = [];
      this.streaming = true;
      this.error = null;
      const ac = new AbortController();
      this.abort = ac;

      const apply = (ev: SSEEvent) => {
        if (ev.type === "token") {
          this.streamingText += ev.data.text ?? "";
        } else if (ev.type === "tool_call") {
          const d = ev.data;
          if (d.status === "start") {
            this.streamingTools.push({ tool: d.tool ?? "?", status: "start" });
          } else {
            // 匹配最近一个还在 start 的同名工具（同一工具可能多轮调用）
            const pending = [...this.streamingTools]
              .reverse()
              .find((t) => t.tool === d.tool && t.status === "start");
            if (pending) {
              pending.status = d.status === "failed" ? "failed" : "done";
              pending.ms = d.ms;
            }
          }
        } else if (ev.type === "error") {
          this.error = { code: ev.data.code ?? "ERROR", message: ev.data.message ?? "出错了" };
        }
      };

      try {
        await api.streamMessage(this.currentSessionId, { message: text, context }, apply, ac.signal);
      } catch (e) {
        if (!ac.signal.aborted) {
          this.error = { code: "NETWORK", message: e instanceof Error ? e.message : "网络错误" };
        }
      } finally {
        if (this.streamingText || this.streamingTools.length) {
          this.messages.push({
            id: -Date.now() - 1,
            role: "assistant",
            content: this.streamingText,
            tool_calls: this.streamingTools.map((t) => ({ ...t })),
            created_at: "",
          });
        }
        this.streamingText = "";
        this.streamingTools = [];
        this.streaming = false;
        this.abort = null;
      }
    },
    cancel() {
      this.abort?.abort();
    },
  },
});
```

- [ ] **Step 4: 写 store 测试**

```ts
// web/src/agent/__tests__/store.spec.ts
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import * as api from "../api";
import { useAgentStore } from "../store";
import type { SSEEvent } from "../types";

vi.mock("../api", () => ({
  listSessions: vi.fn().mockResolvedValue([]),
  createSession: vi.fn().mockResolvedValue({ id: 1, title: null, created_at: "", updated_at: "" }),
  deleteSession: vi.fn().mockResolvedValue(undefined),
  listMessages: vi.fn().mockResolvedValue([]),
  streamMessage: vi.fn(),
}));

describe("agent store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("token 合并 + tool_call 状态机（start→done 带耗时）", async () => {
    (api.streamMessage as Mock).mockImplementation(
      async (_sid: number, _body: unknown, onEvent: (e: SSEEvent) => void) => {
        onEvent({ type: "token", data: { text: "你" } });
        onEvent({ type: "token", data: { text: "好" } });
        onEvent({ type: "tool_call", data: { tool: "invoice_list", status: "start" } });
        onEvent({ type: "tool_call", data: { tool: "invoice_list", status: "done", ms: 12 } });
        onEvent({ type: "done", data: {} });
      },
    );
    const store = useAgentStore();
    store.currentSessionId = 1;
    await store.sendMessage("查发票", { page: "/invoices" });

    const last = store.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.content).toBe("你好");
    expect(last.tool_calls?.[0]).toMatchObject({ tool: "invoice_list", status: "done", ms: 12 });
    expect(store.streaming).toBe(false);
  });

  it("error 事件写入 error 状态", async () => {
    (api.streamMessage as Mock).mockImplementation(
      async (_sid: number, _body: unknown, onEvent: (e: SSEEvent) => void) => {
        onEvent({ type: "error", data: { code: "LLM_TIMEOUT", message: "模型超时" } });
      },
    );
    const store = useAgentStore();
    store.currentSessionId = 1;
    await store.sendMessage("hi", { page: "/" });
    expect(store.error).toEqual({ code: "LLM_TIMEOUT", message: "模型超时" });
  });

  it("cancel 中止流且不记为错误", async () => {
    (api.streamMessage as Mock).mockImplementation(
      (_sid: number, _body: unknown, _onEvent: unknown, signal: AbortSignal) =>
        new Promise((_res, rej) => {
          signal.addEventListener("abort", () => rej(new DOMException("Aborted", "AbortError")));
        }),
    );
    const store = useAgentStore();
    store.currentSessionId = 1;
    const p = store.sendMessage("hi", { page: "/" });
    store.cancel();
    await p;
    expect(store.streaming).toBe(false);
    expect(store.error).toBeNull();
  });
});
```

- [ ] **Step 5: 跑测试**

```bash
cd web && npm run test -- src/agent/__tests__/store.spec.ts   # 期望 3 passed
```

若 `store.messages.at(-1)` 在旧 TS lib 上报错，改 `store.messages[store.messages.length - 1]`。

- [ ] **Step 6: Commit**

```bash
git add web/src/agent/types.ts web/src/agent/api.ts web/src/agent/store.ts web/src/agent/__tests__/store.spec.ts
git commit -m "feat(agent-web): 前端类型 + SSE 客户端 + Pinia 流式状态机"
```

---

### Task 12: 前端组件 + App.vue 挂载

**Files:**
- Create: `web/src/agent/components/FloatingButton.vue`
- Create: `web/src/agent/components/MessageList.vue`
- Create: `web/src/agent/components/MessageInput.vue`
- Create: `web/src/agent/components/AgentDrawer.vue`
- Modify: `web/src/App.vue`
- Test: `web/src/agent/__tests__/MessageList.spec.ts`

**Interfaces:**
- Consumes: Task 11 store/api/types
- Produces: `<FloatingButton />`（v-if 登录）、`<AgentDrawer />`（含会话侧栏/消息区/输入区/上下文 chip）

- [ ] **Step 1: FloatingButton.vue**

```vue
<!-- 右下角悬浮球：点开 Agent 抽屉 -->
<script setup lang="ts">
import { useAgentStore } from "../store";
const store = useAgentStore();
</script>

<template>
  <button class="agent-fab" title="智能助手" @click="store.openDrawer()">AI</button>
</template>

<style scoped>
.agent-fab {
  position: fixed;
  right: 28px;
  bottom: 32px;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  cursor: pointer;
  box-shadow: 0 6px 16px rgba(37, 99, 235, 0.35);
  z-index: 900;
}
.agent-fab:hover {
  filter: brightness(1.06);
}
</style>
```

- [ ] **Step 2: MessageList.vue**

```vue
<!-- 消息列表：用户/助手气泡 + 工具调用 chip（纯文本渲染，white-space: pre-wrap） -->
<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import type { AgentMessage, AgentToolCall } from "../types";

const props = defineProps<{
  messages: AgentMessage[];
  streamingText: string;
  streamingTools: AgentToolCall[];
  streaming: boolean;
}>();

const scroller = ref<HTMLElement | null>(null);
async function scrollToBottom() {
  await nextTick();
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
}
watch(() => [props.messages.length, props.streamingText, props.streamingTools.length], scrollToBottom);

const STATUS_TEXT: Record<string, string> = { start: "运行中", done: "完成", failed: "失败" };
</script>

<template>
  <div ref="scroller" class="msg-scroller">
    <div v-if="!messages.length && !streaming" class="msg-empty">
      你好，我是发票易助手。试试问：「本月有哪些待复核的发票？」
    </div>
    <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
      <div class="msg-text">{{ m.content }}</div>
      <div v-if="m.tool_calls?.length" class="tool-chips">
        <span v-for="(t, i) in m.tool_calls" :key="i" class="tool-chip" :class="t.status">
          {{ t.tool }} · {{ STATUS_TEXT[t.status] ?? t.status }}<template v-if="t.ms"> · {{ t.ms }}ms</template>
        </span>
      </div>
    </div>
    <div v-if="streaming" class="msg assistant">
      <div class="msg-text">{{ streamingText }}<span class="cursor">▍</span></div>
      <div v-if="streamingTools.length" class="tool-chips">
        <span v-for="(t, i) in streamingTools" :key="i" class="tool-chip" :class="t.status">
          {{ t.tool }} · {{ STATUS_TEXT[t.status] ?? t.status }}<template v-if="t.ms"> · {{ t.ms }}ms</template>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg-scroller {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}
.msg-empty {
  color: #8a94a6;
  font-size: 13px;
  margin-top: 24px;
  text-align: center;
}
.msg {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
}
.msg.user {
  align-items: flex-end;
}
.msg-text {
  max-width: 92%;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .msg-text {
  background: #2563eb;
  color: #fff;
}
.msg.assistant .msg-text {
  background: #f2f4f7;
  color: #1f2937;
}
.cursor {
  animation: blink 1s step-start infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
.tool-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.tool-chip {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  background: #eef2ff;
  color: #4f46e5;
}
.tool-chip.done { background: #ecfdf5; color: #059669; }
.tool-chip.failed { background: #fef2f2; color: #dc2626; }
</style>
```

- [ ] **Step 3: MessageInput.vue**

```vue
<!-- 输入区：发送 / 取消 / 重试 -->
<script setup lang="ts">
import { ref } from "vue";
const props = defineProps<{ streaming: boolean; canRetry: boolean }>();
const emit = defineEmits<{ (e: "send", text: string): void; (e: "cancel"): void; (e: "retry"): void }>();
const text = ref("");

function onSend() {
  const t = text.value.trim();
  if (!t || props.streaming) return;
  emit("send", t);
  text.value = "";
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    onSend();
  }
}
</script>

<template>
  <div class="input-box">
    <textarea
      v-model="text"
      class="input-area"
      rows="2"
      placeholder="描述你的问题，Enter 发送 / Shift+Enter 换行"
      @keydown="onKeydown"
    />
    <div class="input-actions">
      <button v-if="canRetry && !streaming" class="btn ghost" @click="emit('retry')">重试</button>
      <button v-if="streaming" class="btn danger" @click="emit('cancel')">停止</button>
      <button v-else class="btn primary" :disabled="!text.trim()" @click="onSend">发送</button>
    </div>
  </div>
</template>

<style scoped>
.input-box {
  border-top: 1px solid #eef0f3;
  padding: 10px 12px;
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.input-area {
  flex: 1;
  resize: none;
  border: 1px solid #e2e5ea;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  outline: none;
}
.input-area:focus {
  border-color: #2563eb;
}
.input-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn.primary { background: #2563eb; color: #fff; }
.btn.primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.danger { background: #fef2f2; color: #dc2626; }
.btn.ghost { background: #f2f4f7; color: #374151; }
</style>
```

- [ ] **Step 4: AgentDrawer.vue**

```vue
<!-- Agent 抽屉：会话切换 + 消息流 + 输入 + 页面上下文 chip -->
<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useAgentStore } from "../store";
import MessageList from "./MessageList.vue";
import MessageInput from "./MessageInput.vue";
import type { ChatContext } from "../types";

const store = useAgentStore();
const route = useRoute();

const contextChip = computed(() => {
  const bits: string[] = [route.path];
  const q = route.query;
  if (q.invoice_id) bits.push(`发票#${q.invoice_id}`);
  if (q.claim_id) bits.push(`报销单#${q.claim_id}`);
  return bits.join(" · ");
});

function buildContext(): ChatContext {
  const q = route.query;
  return {
    page: route.path,
    invoice_id: q.invoice_id ? Number(q.invoice_id) : null,
    claim_id: q.claim_id ? Number(q.claim_id) : null,
    receipt_id: q.receipt_id ? Number(q.receipt_id) : null,
    month: typeof q.month === "string" ? q.month : null,
  };
}

async function onSend(text: string) {
  await store.sendMessage(text, buildContext());
}

function onRetry() {
  const lastUser = [...store.messages].reverse().find((m) => m.role === "user");
  if (lastUser) onSend(lastUser.content);
}
</script>

<template>
  <a-drawer
    :open="store.drawerOpen"
    placement="right"
    :width="440"
    :closable="true"
    :body-style="{ padding: 0, display: 'flex', flexDirection: 'column', height: '100%' }"
    title="智能助手"
    @close="store.closeDrawer()"
  >
    <div class="drawer-head">
      <select
        class="session-select"
        :value="store.currentSessionId ?? undefined"
        @change="store.switchSession(Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="s in store.sessions" :key="s.id" :value="s.id">
          {{ s.title || "新会话" }}
        </option>
      </select>
      <button class="head-btn" @click="store.createSession()">新建</button>
      <button
        class="head-btn danger"
        :disabled="!store.currentSessionId || store.streaming"
        @click="store.currentSessionId && store.removeSession(store.currentSessionId)"
      >删除</button>
    </div>
    <div class="ctx-chip">上下文：{{ contextChip }}</div>
    <MessageList
      :messages="store.messages"
      :streaming-text="store.streamingText"
      :streaming-tools="store.streamingTools"
      :streaming="store.streaming"
    />
    <div v-if="store.error" class="err-banner">
      {{ store.error.message }}（{{ store.error.code }}）
    </div>
    <MessageInput
      :streaming="store.streaming"
      :can-retry="!store.streaming && !!store.error"
      @send="onSend"
      @cancel="store.cancel()"
      @retry="onRetry"
    />
  </a-drawer>
</template>

<style scoped>
.drawer-head {
  display: flex;
  gap: 8px;
  padding: 10px 12px 6px;
}
.session-select {
  flex: 1;
  border: 1px solid #e2e5ea;
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 13px;
  background: #fff;
}
.head-btn {
  border: 1px solid #e2e5ea;
  background: #fff;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.head-btn.danger { color: #dc2626; }
.head-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ctx-chip {
  padding: 0 12px 8px;
  font-size: 12px;
  color: #8a94a6;
}
.err-banner {
  margin: 0 12px 8px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 12px;
}
</style>
```

（说明：会话选择器用原生 `<select>` 而不是 antdv `a-select`——抽屉里的 a-select teleport 弹层在测试与嵌套布局里麻烦，原生 select 零依赖零坑。）

- [ ] **Step 5: App.vue 挂载**

`<script setup>` 加：

```ts
import FloatingButton from "./agent/components/FloatingButton.vue";
import AgentDrawer from "./agent/components/AgentDrawer.vue";
import { useAgentStore } from "./agent/store";

const agent = useAgentStore();
```

`onLogout` 里 `auth.logout().finally(...)` 前加一行 `agent.reset();`。

`<template>` 里 `</a-layout>`（最外层 v-if 的收尾）之后、`<router-view v-else />` 之前加：

```vue
    <template v-if="route.path !== '/login'">
      <floating-button />
      <agent-drawer />
    </template>
```

- [ ] **Step 6: 写组件测试**

```ts
// web/src/agent/__tests__/MessageList.spec.ts
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import MessageList from "../components/MessageList.vue";

describe("MessageList", () => {
  it("渲染消息文本与工具 chip（含失败态）", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 1, role: "user", content: "查发票", tool_calls: null, created_at: "" },
          {
            id: 2, role: "assistant", content: "共 3 张", created_at: "",
            tool_calls: [
              { tool: "invoice_list", status: "done", ms: 12 },
              { tool: "invoice_detail", status: "failed" },
            ],
          },
        ],
        streamingText: "",
        streamingTools: [],
        streaming: false,
      },
    });
    expect(wrapper.text()).toContain("查发票");
    expect(wrapper.text()).toContain("共 3 张");
    expect(wrapper.text()).toContain("invoice_list · 完成 · 12ms");
    expect(wrapper.text()).toContain("invoice_detail · 失败");
  });

  it("流式期间显示光标", () => {
    const wrapper = mount(MessageList, {
      props: { messages: [], streamingText: "正在", streamingTools: [], streaming: true },
    });
    expect(wrapper.text()).toContain("正在");
  });
});
```

- [ ] **Step 7: 跑测试 + 类型检查**

```bash
cd web && npm run test -- src/agent/__tests__   # 期望 5 passed（3 store + 2 组件）
cd web && npx vue-tsc -b --noEmit 2>&1 | tail -5   # 期望无错误
```

- [ ] **Step 8: Commit**

```bash
git add web/src/agent/components/ web/src/agent/__tests__/MessageList.spec.ts web/src/App.vue
git commit -m "feat(agent-web): 悬浮球 + 抽屉 + 消息流/输入组件，App.vue 全局挂载"
```

---

### Task 13: 会话侧栏完善 + 上下文注入 + 使用文档

**Files:**
- Modify: `web/src/agent/components/AgentDrawer.vue`（若 Task 12 已含侧栏则本任务仅验收 + 补「打开抽屉时刷新会话列表」）
- Create: `docs/web-agent-助手使用说明.html`（浅色主题，见用户全局规范）
- Test: 无新增自动化测试（手工验收见 Task 14）

**Interfaces:**
- Consumes: Task 12 组件
- Produces: 打开抽屉自动刷新会话列表（标题更新可见）；`docs/` HTML 使用说明

- [ ] **Step 1: 抽屉打开时刷新列表**

`AgentDrawer.vue` 的 `<script setup>`：把顶部 `import { computed } from "vue";` 改为 `import { computed, watch } from "vue";`，然后加：

```ts
watch(
  () => store.drawerOpen,
  (open) => {
    if (open) void store.loadSessions();
  },
);
```

（`ensureSession` 只在无会话时创建；每次打开刷新是为了让「首轮自动标题」在下一次打开时可见。）

- [ ] **Step 2: 写使用说明 HTML**（浅色主题——用户全局规范）

`docs/web-agent-助手使用说明.html`，结构：入口（右下角悬浮球）→ 能做什么（四类场景举例）→ 工具与权限（角色 → 能调什么）→ 写操作确认规则 → 离线部署说明（LLM 端点由后端配置，前端不直连）→ 常见问题（未启用大模型时的静态说明 / 会话保留 90 天 / 权限不足提示）。参考 `docs/` 下既有 HTML 的风格（浅色背景、简洁卡片式布局），单文件内联 CSS，不引外部资源。

- [ ] **Step 3: 前端全量测试 + 构建**

```bash
cd web && npm run test            # 全量前端测试
cd web && npm run build           # vue-tsc + vite build 无错误
```

- [ ] **Step 4: Commit**

```bash
git add web/src/agent/components/AgentDrawer.vue docs/web-agent-助手使用说明.html
git commit -m "feat(agent-web): 抽屉打开刷新会话列表 + 使用说明文档"
```

---

### Task 14: 端到端冒烟验收

**Files:** 无代码改动（除非冒烟发现 bug——按 systematic-debugging 流程修）

**验收步骤（手工，真实服务）：**

- [ ] **Step 1: 启动**

```bash
cd backend && uv run uvicorn invoicing.main:app --reload      # 8000
cd web && npm run dev                                          # 5173
```

- [ ] **Step 2: 静态兜底路径（无需 LLM）**

浏览器登录 `admin/admin123` → 右下角点「AI」悬浮球 → 抽屉打开 → 输入「你能做什么」→ 期望：分片流式出现静态功能说明（发票查询/报销单/回单/报表四类），无报错。

- [ ] **Step 3: 真实 LLM 路径**

重启后端并带环境变量（示例用 DashScope，按本机实际配置替换）：

```bash
cd backend && INVOICING_LLM_ENABLED=true INVOICING_LLM_API_KEY=sk-xxx \
  uv run uvicorn invoicing.main:app --reload
```

抽屉里依次验证：
1. 「本月有哪些发票？」→ 期望看到 `invoice_list` 工具 chip（运行中 → 完成 · xxxms）+ 中文回答
2. 「帮我建一个差旅报销单」→ 期望 Agent 调 `expense_create`（写操作应先在文字里复述确认——观察是否符合 prompt 约定）
3. 员工角色登录 → 「删除发票 1」→ 期望 `invoice_delete` chip 失败 + 回答提示权限不足（SCOPE_DENIED 路径）
4. 生成中点击「停止」→ 流立即停止，消息保留已收内容
5. 关闭抽屉再打开 → 会话与消息仍在；会话标题为第一句问题

- [ ] **Step 4: 审计核验**

```bash
cd backend && uv run python -c "
from invoicing.db import SessionLocal
from invoicing.models import AuditLog
db = SessionLocal()
for r in db.query(AuditLog).filter(AuditLog.action=='AGENT_CHAT').order_by(AuditLog.id.desc()).limit(5):
    print(r.id, r.user_id, r.channel, r.outcome, r.detail)
"
```
期望：每回合一行，含 session_id / tool_calls 名单 / tokens。

- [ ] **Step 5: 全量回归**

```bash
cd backend && uv run pytest ../test -q          # 全绿
cd web && npm run test                          # 全绿
```

- [ ] **Step 6: 收尾**

冒烟过程中若有修复，按修复内容单独提交（`fix(agent): …`）；全绿且无改动则无需提交。最后确认工作树干净：`git status`。

---

## 风险与回归对照

| 风险 | 本计划的防护 |
|---|---|
| LLM 幻觉 tool_call JSON 卡循环 | capability_guard R3（Task 2 测试覆盖） |
| 跑满轮数无答复（AuditMind 事故） | WRAPUP 收尾轮（Task 4 `test_max_turns_triggers_wrapup`） |
| 工具无限失败 | guard R1/R2/R4 |
| scope 越权 | 工具层 `@requires` 真实守门（Task 6 `test_scope_denied_for_employee`） |
| 请求 session 在流式期间被关闭 | 后台任务用独立 `SessionLocal()`（Task 9） |
| 客户端断连后 LLM 继续烧 token | 响应生成器 finally 置 cancel_event（Task 9） |
| 离线部署连云端 LLM | 复用 `check_offline_llm`（启动 BLOCK）+ 前端不直连（架构层面） |
| 历史无限增长 | agent_retention cron（Task 10） |
| 员工看不到他人会话 | `_own_session` 归属校验（Task 9 测试覆盖） |

## 已知遗留（显式记录，不在本计划范围）

- 写操作 UI 级确认框（需 SSE 暂停/恢复协议）——当前为 prompt 级确认。
- 工具 manifest 按 scope 过滤（需机器可读的 tool→scope 映射）。
- 前端 markdown 渲染、图表渲染。
- 流式 idle 超时的主动熔断（当前靠 openai 客户端 timeout）。
- `agent_messages` 归档策略（>200 条自动归档）。
