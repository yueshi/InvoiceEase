# P0-2 两段握手 —— TDD 执行计划

> 日期：2026-10-10 · 分支：`feat/agent-digital-employee`
> 上游：`design/2026-10-09-P0-协议合规-实施方案.md` Phase 4
> 基线：`frd/Agent-Digital-Employee-Requirements-v1.1.md` §7.5 + §7.5.1
> 预计：2 周（1 人）/ 1 周（2 人并行：一人后端 + 一人前端）

## 范围与依赖

**实施依赖**：
- ✅ P0-1 完成（validate_expense 已就绪，会作为 confirm_execute 里的 preview 调用）
- ⚠️ P0-5（audit 字段）**不在 P0-2 范围**——本 plan 把 channel / actor_type 写进 audit detail（与 P0-1 一致），P0-5 后再升级
- ⚠️ P0-3（白名单）**不在 P0-2 范围**——confirm_execute 作为新工具注册即可，白名单后续统一收紧

**改动面**：
- 新增 2 张表：`proposals` / `idempotency_keys`
- 19 个 MCP 写工具 PATCH 为两段式（`*_proposal` + `confirm_execute`）
- 1 个新 MCP 工具：`confirm_execute`（单一入口）
- 1 个新模块：`backend/src/invoicing/idempotency.py`（idempotent_run / create_proposal / consume_proposal）
- REST API 写端点改两段（preview + confirm），前端 ExpensesView 走 ConfirmModal
- 新增 `web/src/components/ConfirmModal.vue`（高风险子项二次确认）
- 灰度开关：`two_phase_writes_enabled: bool = True`

## 文件结构总览

**新增后端**：
- `backend/src/invoicing/models/proposal.py` —— Proposal 表
- `backend/src/invoicing/models/idempotency.py` —— IdempotencyKey 表
- `backend/src/invoicing/idempotency.py` —— `idempotent_run / create_proposal / consume_proposal / generate_token`
- `backend/src/invoicing/mcp/proposal_registry.py` —— 19 个 `*_proposal` ↔ 实现函数的映射（白名单之前的"软白名单"）
- `backend/alembic/versions/<rev>_proposals.py`
- `backend/alembic/versions/<rev>_idempotency_keys.py`

**修改后端**：
- `backend/src/invoicing/mcp/tools.py` —— 19 个写工具 PATCH 为两段式
- `backend/src/invoicing/mcp/server.py` —— 注册 `confirm_execute` 工具
- `backend/src/invoicing/api/expenses.py` —— REST 写端点改两段
- `backend/src/invoicing/api/invoices.py` —— 同上
- `backend/src/invoicing/api/receipts.py` —— 同上
- `backend/src/invoicing/api/companies.py` —— 同上（如存在）
- `backend/src/invoicing/api/bank_accounts.py` —— 同上（如存在）
- `backend/src/invoicing/config.py` —— 加 `two_phase_writes_enabled: bool = True`

**新增前端**：
- `web/src/components/ConfirmModal.vue` —— 共享二次确认 modal（高风险子项）

**修改前端**：
- `web/src/views/ExpensesView.vue` —— submit/approve/reject 走 ConfirmModal
- `web/src/views/InvoiceListView.vue` / `InvoiceDetailView.vue` —— update/unblock 走 ConfirmModal
- `web/src/views/BankAccountsView.vue` / `SettingsView.vue` —— save/delete 走 ConfirmModal

**测试**：
- `test/test_proposal_model.py`
- `test/test_idempotency_model.py`
- `test/test_idempotency_module.py`（idempotent_run / create_proposal / consume_proposal）
- `test/test_two_phase_writes.py`（19 工具各 2 例）
- `test/test_confirm_execute.py`
- `test/test_rest_two_phase.py`
- `web/src/components/__tests__/ConfirmModal.spec.ts`

## 全局约束

- proposal_token 用 `secrets.token_urlsafe(32)` 生成（64 字符 URL-safe）
- token TTL 15 分钟（`PROPOSAL_TTL_SECONDS = 900`）
- idempotency_key 24 小时窗口（`IDEMPOTENCY_TTL_HOURS = 24`）
- 19 个写工具分两段后，**确认执行的入口只有 `confirm_execute`**，避免散落
- 高风险子项（spec §7.5.1）走 ConfirmModal，与 proposal_token **双层独立**：modal 在 Web 端确认（人类用户已登录），proposal_token 在 confirm_execute 处再确认（MCP 通道要求 human_ack=true）
- ponytail 标记：未启用 P0-5 之前，`actor_type` 用 `user`（MCP 通道也写 `user`，因为 ticket 来自人类意图）；P0-5 后再拆 `agent / user / system`

---

## Task 1：Proposal 模型 + 迁移

**Files**：
- Create: `backend/src/invoicing/models/proposal.py`
- Create: `backend/alembic/versions/<rev>_proposals.py`
- Test: `test/test_proposal_model.py`

**Interfaces**：
- Produces: `class Proposal(Base)` 表 `proposals`，列 `(token, tool_name, payload, preview, actor_id, actor_type, channel, expires_at, consumed_at, created_at)`

### Step 1.1 写失败测试

```python
# test/test_proposal_model.py
from datetime import datetime, timedelta, timezone
import pytest
from invoicing.models.proposal import Proposal


def test_proposal_creation(db):
    p = Proposal(
        token="t_" + "x" * 60, tool_name="expense_create",
        payload={"title": "t"}, preview={"description": "创建报销单：t"},
        actor_id=1, actor_type="user", channel="mcp",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(p); db.commit()
    assert p.token.startswith("t_")
    assert p.consumed_at is None
    assert p.created_at is not None


def test_proposal_token_unique(db):
    p1 = Proposal(token="dup", tool_name="x", payload={}, preview={},
                  actor_id=1, actor_type="user", channel="mcp",
                  expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
    db.add(p1); db.commit()
    p2 = Proposal(token="dup", tool_name="x", payload={}, preview={},
                  actor_id=1, actor_type="user", channel="mcp",
                  expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
    db.add(p2)
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        db.commit()


def test_proposal_payload_json(db):
    p = Proposal(
        token="p1", tool_name="expense_create",
        payload={"title": "出差", "amount": 110, "nested": {"k": "v"}},
        preview={"description": "..."},
        actor_id=1, actor_type="user", channel="mcp",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(p); db.commit()
    db.refresh(p)
    assert p.payload["title"] == "出差"
    assert p.payload["nested"]["k"] == "v"
```

Run: `cd backend && uv run pytest ../test/test_proposal_model.py -v`
Expected: FAIL（`proposal` 模块不存在）

### Step 1.2 实现模型

```python
# backend/src/invoicing/models/proposal.py
"""v1.1 §7.5 两段握手中的 proposal 表：token 一次有效，15 分钟 TTL。"""
from datetime import datetime
from sqlalchemy import JSON, Column, DateTime, Integer, String
from invoicing.db import Base


class Proposal(Base):
    __tablename__ = "proposals"
    token = Column(String(64), primary_key=True)
    tool_name = Column(String(64), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    preview = Column(JSON, nullable=False)
    actor_id = Column(Integer, nullable=False)
    actor_type = Column(String(16), nullable=False, default="user")
    channel = Column(String(16), nullable=False, default="mcp")
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=datetime.utcnow, nullable=False)
```

### Step 1.3 手写迁移

```python
# backend/alembic/versions/<rev>_proposals.py
"""proposals 表（P0-2 两段握手）"""
from alembic import op
import sqlalchemy as sa

revision = "<rev>"
down_revision = "d5e6f7a8b9c2"  # 接 dept 字段迁移

def upgrade():
    op.create_table(
        "proposals",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("preview", sa.JSON, nullable=False),
        sa.Column("actor_id", sa.Integer, nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False, server_default="user"),
        sa.Column("channel", sa.String(16), nullable=False, server_default="mcp"),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("consumed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_proposals_tool_name", "proposals", ["tool_name"])
    op.create_index("ix_proposals_expires_at", "proposals", ["expires_at"])

def downgrade():
    op.drop_index("ix_proposals_expires_at", table_name="proposals")
    op.drop_index("ix_proposals_tool_name", table_name="proposals")
    op.drop_table("proposals")
```

### Step 1.4 跑测试

Expected: 3 PASS

### Step 1.5 提交

```bash
git add backend/src/invoicing/models/proposal.py backend/alembic/versions/*_proposals.py test/test_proposal_model.py
git commit -m "feat(proposal): Proposal 表 + 迁移（token 一次有效，15 分钟 TTL）"
```

---

## Task 2：IdempotencyKey 模型 + 迁移

**Files**：
- Create: `backend/src/invoicing/models/idempotency.py`
- Create: `backend/alembic/versions/<rev>_idempotency_keys.py`
- Test: `test/test_idempotency_model.py`

### Step 2.1 写失败测试

```python
# test/test_idempotency_model.py
import pytest
from invoicing.models.idempotency import IdempotencyKey
from sqlalchemy.exc import IntegrityError


def test_idempotency_key_creation(db):
    k = IdempotencyKey(
        key="client-req-001", tool_name="expense_create",
        response={"ok": True, "claim_id": 42},
    )
    db.add(k); db.commit()
    assert k.created_at is not None


def test_idempotency_unique_per_tool(db):
    k1 = IdempotencyKey(key="k1", tool_name="expense_create", response={"a": 1})
    db.add(k1); db.commit()
    k2 = IdempotencyKey(key="k1", tool_name="expense_create", response={"a": 2})
    db.add(k2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_idempotency_same_key_different_tool_allowed(db):
    """同 key 不同 tool 不冲突（防止全局 key 碰撞）。"""
    k1 = IdempotencyKey(key="shared", tool_name="expense_create", response={"a": 1})
    k2 = IdempotencyKey(key="shared", tool_name="invoice_update", response={"b": 2})
    db.add_all([k1, k2]); db.commit()
    assert db.query(IdempotencyKey).count() == 2
```

### Step 2.2 实现

```python
# backend/src/invoicing/models/idempotency.py
"""幂等键表：24 小时窗口，同 (key, tool_name) 重放返回缓存结果（v1.1 §7.5）。"""
from datetime import datetime
from sqlalchemy import JSON, Column, DateTime, String, UniqueConstraint
from invoicing.db import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key = Column(String(64), primary_key=True)
    tool_name = Column(String(64), primary_key=True)
    response = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
```

注：复合主键 (key, tool_name) 实现 §2.2 测试"同 key 不同 tool 不冲突"。

### Step 2.3 迁移

类似 Task 1，含复合主键 `(key, tool_name)` + `expires_at` 索引。

### Step 2.4 跑测试

Expected: 3 PASS

### Step 2.5 提交

```bash
git add backend/src/invoicing/models/idempotency.py backend/alembic/versions/*_idempotency_keys.py test/test_idempotency_model.py
git commit -m "feat(idempotency): IdempotencyKey 表 + 迁移（24h 窗口，复合主键防 tool 碰撞）"
```

---

## Task 3：idempotency 工具模块

**Files**：
- Create: `backend/src/invoicing/idempotency.py`
- Test: `test/test_idempotency_module.py`

**Interfaces**：
- `idempotent_run(db, *, key, tool_name, fn) -> dict`：有 key 走缓存；否则调 fn 缓存
- `create_proposal(db, *, tool_name, payload, preview, actor_id, actor_type, channel, ttl_seconds=900) -> Proposal`
- `consume_proposal(db, *, token, human_ack) -> Proposal`：校验 token + human_ack，标记 consumed
- `generate_token() -> str`：`secrets.token_urlsafe(32)`

### Step 3.1 写失败测试

```python
# test/test_idempotency_module.py
import secrets
from invoicing.idempotency import (
    idempotent_run, create_proposal, consume_proposal, generate_token,
)


def test_generate_token_url_safe_64():
    t = generate_token()
    assert len(t) >= 32
    # url-safe 字符集
    assert all(c.isalnum() or c in "-_" for c in t)


def test_idempotent_run_caches_result(db):
    calls = []
    def fn():
        calls.append(1)
        return {"result": "first"}
    # 第一次：调 fn，缓存
    r1 = idempotent_run(db, key="k1", tool_name="t1", fn=fn)
    assert r1 == {"result": "first"}
    assert len(calls) == 1
    # 第二次：同 key → 返回缓存，不调 fn
    r2 = idempotent_run(db, key="k1", tool_name="t1", fn=fn)
    assert r2 == {"result": "first"}
    assert len(calls) == 1


def test_idempotent_run_no_key_calls_each_time(db):
    calls = []
    def fn():
        calls.append(1)
        return {"n": len(calls)}
    r1 = idempotent_run(db, key=None, tool_name="t1", fn=fn)
    r2 = idempotent_run(db, key=None, tool_name="t1", fn=fn)
    assert r1["n"] == 1
    assert r2["n"] == 2


def test_create_proposal_returns_token(db):
    from invoicing.models.proposal import Proposal
    p = create_proposal(db, tool_name="expense_create",
                         payload={"title": "t"}, preview={"desc": "..."},
                         actor_id=1, actor_type="user", channel="mcp")
    assert p.token is not None
    assert p.expires_at is not None
    assert p.consumed_at is None
    assert db.query(Proposal).count() == 1


def test_consume_proposal_marks_consumed(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                         actor_id=1, actor_type="user", channel="mcp")
    consumed = consume_proposal(db, token=p.token, human_ack=True)
    assert consumed.consumed_at is not None


def test_consume_proposal_human_ack_required(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                         actor_id=1, actor_type="user", channel="mcp")
    import pytest
    with pytest.raises(ValueError, match="human_ack=true"):
        consume_proposal(db, token=p.token, human_ack=False)


def test_consume_proposal_expired_rejected(db):
    from datetime import datetime, timedelta, timezone
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                         actor_id=1, actor_type="user", channel="mcp",
                         ttl_seconds=-1)  # 立即过期
    import pytest
    with pytest.raises(ValueError, match="expired"):
        consume_proposal(db, token=p.token, human_ack=True)


def test_consume_proposal_already_consumed_rejected(db):
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                         actor_id=1, actor_type="user", channel="mcp")
    consume_proposal(db, token=p.token, human_ack=True)
    import pytest
    with pytest.raises(ValueError, match="already consumed"):
        consume_proposal(db, token=p.token, human_ack=True)
```

### Step 3.2 跑测试确认失败

Expected: FAIL（idempotency 模块不存在）

### Step 3.3 实现

```python
# backend/src/invoicing/idempotency.py
"""v1.1 §7.5 两段握手 + 幂等键：工具模块。

提供：
- idempotent_run：同 (key, tool_name) 重放返回缓存（24h 窗口）
- create_proposal：签发一次性 token（默认 15 分钟 TTL）
- consume_proposal：校验 + 标记 consumed；要求 human_ack=True
- generate_token：URL-safe 64 字符 token
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select, and_

from invoicing.models.idempotency import IdempotencyKey
from invoicing.models.proposal import Proposal


IDEMPOTENCY_TTL_HOURS = 24
PROPOSAL_TTL_SECONDS = 900  # 15 分钟


def generate_token() -> str:
    return secrets.token_urlsafe(32)  # ~43 字符 URL-safe


def idempotent_run(db, *, key: str | None, tool_name: str, fn: Callable[[], dict]) -> dict:
    """有 key 走缓存；否则每次都调 fn。"""
    if not key:
        return fn()
    # 检查缓存
    existing = db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.key == key,
            IdempotencyKey.tool_name == tool_name,
            IdempotencyKey.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.response
    # 调 fn，缓存结果
    result = fn()
    db.add(IdempotencyKey(
        key=key, tool_name=tool_name, response=result,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
    ))
    db.commit()
    return result


def create_proposal(db, *, tool_name: str, payload: dict, preview: dict,
                     actor_id: int, actor_type: str, channel: str,
                     ttl_seconds: int = PROPOSAL_TTL_SECONDS) -> Proposal:
    token = generate_token()
    p = Proposal(
        token=token, tool_name=tool_name, payload=payload, preview=preview,
        actor_id=actor_id, actor_type=actor_type, channel=channel,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def consume_proposal(db, *, token: str, human_ack: bool) -> Proposal:
    """校验 token + human_ack，标记 consumed。返回 Proposal 让调用方取 payload 落库。"""
    if not human_ack:
        raise ValueError("human_ack=true required to consume proposal")
    p = db.execute(
        select(Proposal).where(Proposal.token == token)
    ).scalar_one_or_none()
    if p is None:
        raise ValueError(f"proposal not found: {token}")
    # 兼容 naive 与 tz-aware
    expires = p.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise ValueError(f"proposal expired: {token}")
    if p.consumed_at is not None:
        raise ValueError(f"proposal already consumed: {token}")
    p.consumed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(p)
    return p
```

### Step 3.4 跑测试

Expected: 8 PASS

### Step 3.5 提交

```bash
git add backend/src/invoicing/idempotency.py test/test_idempotency_module.py
git commit -m "feat(idempotency): idempotent_run / create_proposal / consume_proposal 模块"
```

---

## Task 4：proposal_registry 软白名单

**Files**：
- Create: `backend/src/invoicing/mcp/proposal_registry.py`
- Test: `test/test_proposal_registry.py`

**目的**：在 P0-3 白名单未到位之前，先用"软白名单"枚举 19 个写工具的 proposal 函数，避免散落。

### Step 4.1 写测试

```python
# test/test_proposal_registry.py
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY, get_proposal


def test_registry_has_19_entries():
    """19 个写工具的 *_proposal 全部注册（spec §5.2 涵盖 + 当前 MCP 全部写）。"""
    assert len(PROPOSAL_REGISTRY) == 19


def test_registry_keys_match_spec():
    expected_subset = {
        "expense_create", "expense_add_entry", "expense_add_invoices",
        "expense_add_receipt", "expense_add_voucher", "expense_submit",
        "expense_approve", "invoice_update", "invoice_delete",
        "invoice_unblock", "invoice_classify", "invoice_ai_review",
        "sales_invoice_import", "red_invoice_link", "receipt_ingest",
        "receipt_pair", "company_info_save", "company_info_delete",
        "bank_account_save", "bank_account_delete",
    }
    assert expected_subset.issubset(set(PROPOSAL_REGISTRY.keys()))


def test_get_proposal_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        get_proposal("nonexistent_tool")
```

### Step 4.2 实现（先建空壳，后逐步填 19 个）

```python
# backend/src/invoicing/mcp/proposal_registry.py
"""P0-2 软白名单：19 个写工具的 *_proposal 函数注册表。

P0-3 之后这层用 server 枚举白名单替代；现在先确保 confirm_execute 只能 confirm
注册过的 tool_name。
"""
PROPOSAL_REGISTRY: dict[str, callable] = {}


def register_proposal(tool_name: str):
    """装饰器：注册 *_proposal 函数。"""
    def deco(fn):
        PROPOSAL_REGISTRY[tool_name] = fn
        return fn
    return deco


def get_proposal(tool_name: str) -> callable:
    if tool_name not in PROPOSAL_REGISTRY:
        raise KeyError(f"tool not registered for two-phase: {tool_name}")
    return PROPOSAL_REGISTRY[tool_name]
```

### Step 4.3 跑测试

Expected: 3 FAIL（registry 为空）→ 下游 Task 5-7 逐步填满

### Step 4.4 提交

```bash
git add backend/src/invoicing/mcp/proposal_registry.py test/test_proposal_registry.py
git commit -m "feat(mcp): proposal_registry 软白名单（19 工具占位，P0-3 升级为硬白名单）"
```

---

## Task 5：confirm_execute 单一入口

**Files**：
- Modify: `backend/src/invoicing/mcp/server.py`（注册新工具）
- Modify: `backend/src/invoicing/mcp/tools.py`（实现）
- Test: `test/test_confirm_execute.py`

### Step 5.1 写失败测试

```python
# test/test_confirm_execute.py
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.mcp.tools import confirm_execute


def test_confirm_execute_unknown_tool_raises(db):
    """未注册 tool_name → KeyError。"""
    import pytest
    with pytest.raises(KeyError):
        confirm_execute(db, token="any", tool_name="never_registered",
                         human_ack=True)


def test_confirm_execute_invalid_token_raises(db, mcp_auth):
    """token 不存在 → ValueError。"""
    from invoicing.models import Role, User
    u = User(username="u_ce", password_hash="x", role=Role.employee.value)
    db.add(u); db.commit()
    mcp_auth(u)
    import pytest
    with pytest.raises(ValueError, match="not found"):
        confirm_execute(db, token="nonexistent_token", tool_name="x", human_ack=True)


def test_confirm_execute_human_ack_required(db, mcp_auth):
    """human_ack=False → ValueError（不允许静默放过）。"""
    from invoicing.idempotency import create_proposal
    from invoicing.models import Role, User
    u = User(username="u_ce2", password_hash="x", role=Role.employee.value)
    db.add(u); db.commit()
    mcp_auth(u)
    p = create_proposal(db, tool_name="x", payload={}, preview={},
                         actor_id=u.id, actor_type="user", channel="mcp")
    import pytest
    with pytest.raises(ValueError, match="human_ack=true"):
        confirm_execute(db, token=p.token, tool_name="x", human_ack=False)
```

### Step 5.2 实现 confirm_execute

```python
# backend/src/invoicing/mcp/tools.py 追加

from invoicing.idempotency import consume_proposal
from invoicing.mcp.proposal_registry import get_proposal


def confirm_execute(db, *, token: str, tool_name: str, human_ack: bool,
                      idempotency_key: str | None = None) -> dict:
    """v1.1 §7.5 两段握手的第二步入口。

    校验：token 存在 + human_ack=true + tool_name 在白名单。
    校验通过后调对应 *_proposal 注册函数落库。
    """
    from invoicing.idempotency import idempotent_run
    from invoicing.audit import write_audit
    from invoicing.mcp.identity import current_principal

    # 1. 校验 tool 在白名单
    try:
        proposal_fn = get_proposal(tool_name)
    except KeyError as e:
        raise KeyError(str(e))

    # 2. 校验 token + human_ack + 标记 consumed
    proposal = consume_proposal(db, token=token, human_ack=human_ack)

    # 3. 调注册函数落库（带幂等）
    principal = current_principal()
    actor_id = principal.user_id or 0
    actor_type = "user" if principal.user_id else "system"
    channel = "mcp"

    def _execute():
        result = proposal_fn(db, **proposal.payload)
        # 审计
        write_audit(
            db, action=f"{tool_name.upper()}_CONFIRMED", user_id=actor_id,
            channel=channel,
            detail={
                "proposal_token": token,
                "result": str(result) if result else None,
            },
        )
        db.commit()
        return result

    return idempotent_run(db, key=idempotency_key, tool_name=tool_name, fn=_execute)
```

### Step 5.3 注册到 server.py

```python
# backend/src/invoicing/mcp/server.py 追加
from invoicing.mcp import tools as mcp_tools

@server.tool(
    description="确认执行一个待执行的提案。两段握手的第二步：必须 human_ack=true，proposal_token 由 *_proposal 返回。所有写操作的统一入口。",
)
def confirm_execute(token: str, tool_name: str, human_ack: bool,
                      idempotency_key: str | None = None) -> dict:
    return mcp_tools.confirm_execute(
        token=token, tool_name=tool_name, human_ack=human_ack,
        idempotency_key=idempotency_key,
    )
```

### Step 5.4 跑测试

Expected: 3 PASS（基础防护）

### Step 5.5 提交

```bash
git add backend/src/invoicing/mcp/server.py backend/src/invoicing/mcp/tools.py test/test_confirm_execute.py
git commit -m "feat(mcp): confirm_execute 单一入口（两段握手的第二步，校验+幂等+审计）"
```

---

## Task 6-10：19 个写工具逐批 PATCH 为两段式

**模式（每个工具）**：
```python
# 原
@requires("expense:write")
def expense_create(title, remark, claim_type) -> dict:
    return svc.create_claim(db, _current_user(db), title, remark, claim_type).as_dict()

# 改为
@requires("expense:write")
@register_proposal("expense_create")
def expense_create_proposal(title, remark, claim_type,
                              idempotency_key: str | None = None) -> dict:
    """返回 proposal_token + preview，不落库。"""
    from invoicing.idempotency import create_proposal, idempotent_run
    from invoicing.mcp.identity import current_principal

    def _build():
        principal = current_principal()
        actor_id = principal.user_id or 0
        p = create_proposal(
            db, tool_name="expense_create",
            payload={"title": title, "remark": remark, "claim_type": claim_type},
            preview={
                "description": f"创建报销单：{title}",
                "claim_type": claim_type,
            },
            actor_id=actor_id, actor_type="user", channel="mcp",
        )
        return {"proposal_token": p.token, "preview": p.preview,
                "expires_at": p.expires_at.isoformat()}

    return idempotent_run(db, key=idempotency_key, tool_name="expense_create", fn=_build)


def expense_create(db, *, title, remark, claim_type) -> dict:
    """实际落库函数（被 confirm_execute 调用）。"""
    claim = svc.create_claim(db, _current_user(db), title, remark, claim_type)
    return {"id": claim.id, "claim_no": claim.claim_no, "status": claim.status.value}
```

**注意事项**：
- 每个工具写 2 个测试：proposal 返回 token / confirm 落库
- 原工具的 `db` 入参要改成显式（confirm_execute 调的是 `proposal_fn(db, **payload)`，需要 db 是入参）
- `_current_user(db)` 仍可调（不破坏现有 RBAC）
- 工具级 `@requires` scope 不变（前端/IM 仍按现有 scope 控制）

### 分批落地

| Task | 范围 | 工具数 | 预计 |
|---|---|---|---|
| Task 6 | expense_* 系列 | 7 | 0.5 天 |
| Task 7 | invoice_* 系列 | 6 | 0.5 天 |
| Task 8 | receipt_* / sales_* / red_* 系列 | 4 | 0.5 天 |
| Task 9 | company_info_* / bank_account_* 系列 | 4 | 0.5 天 |

**每 Task 流程**：
1. 写 2 × N 个测试（proposal + confirm 各 1）
2. PATCH N 个工具（改写 + 落库函数拆分）
3. 跑测试
4. 提交

### Task 6 示例（expense_* 7 个工具）

```python
# test/test_two_phase_expense.py
import pytest
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY
from invoicing.mcp.tools import (
    expense_create_proposal, expense_submit_proposal, expense_approve_proposal,
    # ...
)
from invoicing.mcp.tools import confirm_execute
from invoicing.models import Role, User
from invoicing.models.expense import ExpenseClaim, ExpenseClaimStatus


def test_expense_create_proposal_returns_token(db, mcp_auth):
    u = User(username="u_t1", password_hash="x", role=Role.employee.value)
    db.add(u); db.commit()
    mcp_auth(u)
    r = expense_create_proposal(db, title="差旅", remark=None, claim_type="travel",
                                  idempotency_key="req-001")
    assert "proposal_token" in r
    assert r["preview"]["description"].startswith("创建报销单")


def test_confirm_expense_create_executes(db, mcp_auth):
    u = User(username="u_t2", password_hash="x", role=Role.employee.value)
    db.add(u); db.commit()
    mcp_auth(u)
    r = expense_create_proposal(db, title="差旅", remark=None, claim_type="travel")
    claim = confirm_execute(db, token=r["proposal_token"],
                              tool_name="expense_create", human_ack=True)
    assert "id" in claim
    assert db.query(ExpenseClaim).count() == 1
```

```bash
git commit -m "feat(mcp): expense_* 7 个写工具 PATCH 两段式（proposal + confirm）"
```

（其他三批 Task 7-9 同模式；每批 4-6 个工具；测试 ~14-18 例；commit 各 1 个。）

---

## Task 10：proposal_registry 完整性测试

确认 PROPOSAL_REGISTRY 19 个都注册了 + 都能走通两段。

```python
# test/test_two_phase_full_coverage.py
from invoicing.mcp.proposal_registry import PROPOSAL_REGISTRY


def test_all_19_proposal_functions_registered():
    expected = {
        "expense_create", "expense_add_entry", "expense_add_invoices",
        "expense_add_receipt", "expense_add_voucher", "expense_submit",
        "expense_approve", "invoice_update", "invoice_delete",
        "invoice_unblock", "invoice_classify", "invoice_ai_review",
        "sales_invoice_import", "red_invoice_link", "receipt_ingest",
        "receipt_pair", "company_info_save", "company_info_delete",
        "bank_account_save", "bank_account_delete",
    }
    assert set(PROPOSAL_REGISTRY.keys()) == expected


def test_all_19_registered_with_import():
    """保证 import 副作用让所有 *_proposal 都注册。"""
    from invoicing.mcp import tools  # 触发 import
    assert len(PROPOSAL_REGISTRY) == 20  # 实际个数（spec 19 + 预留扩展）
```

### Task 10 提交

```bash
git commit -m "test(mcp): proposal_registry 全 19 工具注册覆盖"
```

---

## Task 11：REST API 改两段

**Files**：
- Modify: `backend/src/invoicing/api/expenses.py`（POST /expenses、/entries、/invoices、/submit、/approve）
- Modify: `backend/src/invoicing/api/invoices.py`（PUT /invoices/{id}、DELETE 等）
- Modify: `backend/src/invoicing/api/receipts.py`（POST /receipts/ingest、/pair）
- Test: `test/test_rest_two_phase.py`

**模式（每个端点）**：
```python
# 原：POST /expenses → 直接 svc.create_claim
# 改：POST /expenses → 返回 preview + proposal_token
#     POST /expenses/confirm → 调 confirm_execute
```

```python
# api/expenses.py
@router.post("/expenses/preview", response_model=ProposalOut)
def preview_expense(payload: ExpenseCreate, current_user: User = Depends(...),
                      idempotency_key: str | None = Header(None)):
    """第一步：预览提案（不落库）。"""
    return expense_create_proposal(
        db, title=payload.title, remark=payload.remark,
        claim_type=payload.claim_type, idempotency_key=idempotency_key,
    )

@router.post("/expenses/confirm", response_model=ExpenseClaimOut)
def confirm_expense(payload: ConfirmRequest, current_user: User = Depends(...)):
    """第二步：确认执行。"""
    return confirm_execute(
        db, token=payload.proposal_token, tool_name="expense_create",
        human_ack=True, idempotency_key=payload.idempotency_key,
    )
```

### Step 11.1 写测试

```python
# test/test_rest_two_phase.py
def test_rest_expense_two_phase_full_flow(client, db, mcp_admin_auth):
    # 1) preview
    r1 = client.post("/api/v1/expenses/preview", json={"title": "差旅", "claim_type": "travel"},
                       headers={"Idempotency-Key": "req-1"})
    assert r1.status_code == 200
    token = r1.json()["proposal_token"]
    # 2) confirm
    r2 = client.post("/api/v1/expenses/confirm",
                       json={"proposal_token": token, "idempotency_key": "req-1"})
    assert r2.status_code == 200
    assert r2.json()["claim_no"].startswith("FY-")
    # 3) 第二次同 idempotency_key → 返回缓存
    r3 = client.post("/api/v1/expenses/confirm",
                       json={"proposal_token": "any", "idempotency_key": "req-1"})
    # 走缓存：可能因为 token 已用而报错（视具体实现）
    # 关键：不会创建第二张报销单
    assert db.query(ExpenseClaim).count() == 1
```

### Step 11.2-11.5 PATCH + 跑测试 + 提交

```bash
git commit -m "refactor(api): REST 写端点改两段（preview + confirm），头 Idempotency-Key"
```

---

## Task 12：Web ConfirmModal 组件

**Files**：
- Create: `web/src/components/ConfirmModal.vue`
- Test: `web/src/components/__tests__/ConfirmModal.spec.ts`

**Props**：
```ts
interface Props {
  open: boolean
  title: string
  preview: Record<string, any>
  riskLevel: 'low' | 'medium' | 'high'
  highRiskAction?: string
  requireReason?: boolean
}
emits: ['confirm', 'cancel', 'update:open']
```

**行为**：
- low/medium：直接展示 preview + 确认/取消
- high（§7.5.1）：额外 reason 输入 + 按钮文字"我已确认执行"

### Step 12.1 写测试

```typescript
// web/src/components/__tests__/ConfirmModal.spec.ts
import { mount } from '@vue/test-utils'
import ConfirmModal from '../ConfirmModal.vue'

describe('ConfirmModal', () => {
  it('emits confirm when low risk and clicked', async () => {
    const wrapper = mount(ConfirmModal, {
      props: { open: true, title: '确认提交', preview: { a: 1 }, riskLevel: 'low' },
    })
    await wrapper.find('button.confirm').trigger('click')
    expect(wrapper.emitted('confirm')).toBeTruthy()
  })

  it('requires reason for high risk', async () => {
    const wrapper = mount(ConfirmModal, {
      props: { open: true, title: '放行拦截', preview: {}, riskLevel: 'high',
                requireReason: true },
    })
    // 不填 reason 不能点确认
    const btn = wrapper.find('button.confirm')
    expect(btn.attributes('disabled')).toBeDefined()
    // 填 reason 后可点
    await wrapper.find('textarea').setValue('业务真实')
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('emits cancel on backdrop click', async () => {
    const wrapper = mount(ConfirmModal, {
      props: { open: true, title: 'x', preview: {}, riskLevel: 'low' },
    })
    await wrapper.find('.modal-mask').trigger('click')
    expect(wrapper.emitted('cancel')).toBeTruthy()
  })
})
```

### Step 12.2-12.5 实现 + 测试 + 提交

```bash
git commit -m "feat(web): ConfirmModal 共享组件（low/medium/high 三档）"
```

---

## Task 13：ExpensesView 集成 ConfirmModal（高风险子项）

**Files**：
- Modify: `web/src/views/ExpensesView.vue`
- Test: `web/src/views/__tests__/ExpensesView.spec.ts`

高风险子项（spec §7.5.1）：
- `expense_submit`（金额 ≥ large_amount_threshold）
- `expense_approve`（任意金额，但有金额风险）
- `bank_account_save` / `delete`
- `invoice_unblock`
- 大额 submit 自动 high

### Step 13.1 写测试

```typescript
it('大额报销 submit 弹 high risk modal', async () => {
  // mock 报销金额 8000（≥ 5000 阈值）
  // 触发 submit
  // 期望：ConfirmModal 出现 riskLevel='high'
  // 期望：必须填 reason
})
```

### Step 13.2-13.5 PATCH + 测试 + 提交

```bash
git commit -m "feat(web): ExpensesView 走 ConfirmModal（高风险子项需 reason）"
```

---

## Task 14：灰度开关 + 端到端测试

**Files**：
- Modify: `backend/src/invoicing/config.py`（加 `two_phase_writes_enabled: bool = True`）
- Test: `test/test_p0_2_e2e.py`

```python
# config.py
two_phase_writes_enabled: bool = True  # P0-2 默认开；上线初期可关
```

### Step 14.1 端到端测试

```python
# test/test_p0_2_e2e.py
def test_e2e_two_phase_full_flow(client, mcp_admin_auth, db):
    # Agent 通道：proposal → confirm
    # 1) LLM 调 expense_create_proposal
    r1 = expense_create_proposal(db, title="差旅", claim_type="travel")
    token = r1["proposal_token"]
    # 2) 用户在 Web 看到 modal，confirm
    r2 = confirm_execute(db, token=token, tool_name="expense_create", human_ack=True)
    # 3) 重放：同 idempotency_key → 缓存结果
    r3 = confirm_execute(db, token=token, tool_name="expense_create",
                          human_ack=True, idempotency_key="req-1")
    # 4) 校验：DB 只有 1 张报销单
    assert db.query(ExpenseClaim).count() == 1


def test_e2e_double_click_protected(client, mcp_admin_auth, db):
    """模拟双击：第二次 proposal 应被幂等键拦截。"""
    r1 = expense_create_proposal(db, title="差旅", claim_type="travel",
                                   idempotency_key="req-double")
    # 第二次同 key（模拟用户连点）→ 拿到相同 token，不创建新 proposal
    r2 = expense_create_proposal(db, title="差旅", claim_type="travel",
                                   idempotency_key="req-double")
    assert r1["proposal_token"] == r2["proposal_token"]


def test_e2e_prompt_injection_blocked(client, mcp_admin_auth, db):
    """Prompt injection：Agent 想 human_ack=true 但服务端仍校验。"""
    # 实际 human_ack=False 时服务端 400
    r1 = expense_create_proposal(db, title="x", claim_type="travel")
    import pytest
    with pytest.raises(ValueError, match="human_ack=true"):
        confirm_execute(db, token=r1["proposal_token"], tool_name="expense_create",
                          human_ack=False)
```

### Step 14.2 跑全套

```bash
cd backend && uv run pytest ../test -q --ignore=../test/test_mcp_http.py --ignore=../test/test_mcp_integration.py --ignore=../test/test_agently_fetcher.py
```

Expected: 全部通过；新增 ~50 例（19 工具 × 2 + 模块测试 + e2e）

### Step 14.3 提交

```bash
git add backend/src/invoicing/config.py test/test_p0_2_e2e.py
git commit -m "feat(p0-2): 灰度开关 + 端到端测试（双击拦截 / prompt injection 防御）"
```

---

## Task 15：上线运维手册

**Files**：
- Create: `docs/P0-2-two-phase-rollout.md`

**内容**：
- 灰度开关：`INVOICING_TWO_PHASE_WRITES_ENABLED=false` 临时关两段
- 监控：审计表 `action LIKE '%_CONFIRMED'` 统计
- 回滚：开关 + alembic downgrade
- 已知限制：MCP 工具 id 与 REST id 不互通（跨通道的 idempotency_key 各自独立）

### Task 15 提交

```bash
git add docs/P0-2-two-phase-rollout.md
git commit -m "docs: P0-2 两段握手灰度上线手册"
```

---

## 全局验收

| 维度 | 数字 |
|---|---|
| 新增测试 | ~70（19 工具 × 2 + 模块 + e2e + frontend）|
| 新增表 | 2（proposals / idempotency_keys）|
| 改造工具 | 19 MCP 写工具 |
| 改造 REST 端点 | ~20 个 |
| 改造前端 | 1 个共享组件 + ExpensesView 集成 |
| 全套测试 | 期望 ~800 通过 / 0 回归 |

## Review Focus（按 writing-plans 自检）

| spec 条款 | 任务 | 覆盖 |
|---|---|---|
| §7.5 两段握手（proposal + confirm） | Task 1, 5, 6-10 | ✅ |
| §7.5 写操作幂等键 | Task 2, 3 | ✅ |
| §7.5 15 分钟 TTL | Task 1, 3 | ✅ |
| §7.5.1 高风险子项二次确认 | Task 12, 13 | ✅ |
| §7.5 `human_ack=true` 必传 | Task 3, 5 | ✅ |
| §7.5 服务端枚举白名单 | Task 4（软）+ P0-3 升级 | 🟡 软白名单 |
| §9.5.3 审计 actor/channel | Task 5（用 detail 暂存，P0-5 升级） | 🟡 |

**未涵盖（后续 P0）**：
- P0-3 硬白名单（当前 PROPOSAL_REGISTRY 是软白名单）
- P0-5 audit 字段升级（当前 actor_type / channel 在 detail JSON）
- IM 通道 confirm（spec §7.5；当前只接 MCP 通道）

## 时间预算

| 任务 | 工作量 |
|---|---|
| 1-3 模型 + 模块 | 1.5 天 |
| 4-5 registry + confirm_execute | 0.5 天 |
| 6-9 19 工具 PATCH | 3 天（一人）/ 1.5 天（两人并行）|
| 10 覆盖测试 | 0.5 天 |
| 11 REST API | 1 天 |
| 12-13 前端 | 1 天（一人）/ 0.5 天（两人）|
| 14 灰度 + e2e | 0.5 天 |
| 15 文档 | 0.5 天 |
| **合计** | **8.5 天**（1 人）/ **5 天**（2 人）|

## 下一步

执行方式：
- **Native**（推荐）：本会话按 Task 1→15 顺序做。Task 6-9 是 4 批 ~50 例测试 + 19 工具 PATCH，可以 1 天集中推完
- **Subagent-driven**：每 Task 派 subagent 实施 + 独立 reviewer。适合 4+ 小时的长 Task
- **Phase-by-Phase**：本计划内 4 个阶段（基础 1-3 / confirm 4-5 / 19 工具 6-9 / REST+前端 11-13 / 收尾 14-15）

要我直接开 Task 1 吗？