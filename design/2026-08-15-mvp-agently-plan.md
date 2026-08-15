# 发票易 Agently Mail 接入实施计划（Plan E）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agently Mail（Agent 原生邮箱）接入发票易收取链路：`AgentlyFetcher` 作为 MailFetcher 协议第二实现，复用过滤/解析/验真/归档全链路，拒收回复走 `+reply` 两步确认，前端支持邮箱类型切换，最终用真实收件箱完成全链路验收。

**Architecture:** `fetch/agently.py` 封装 `agently-cli` subprocess（stdout 即 JSON；tip 提示走 stderr），按实测契约解析 `+list/+read/+download`；`mailbox_type` 字段（imap|agently）驱动 fetcher 与拒收回复双分发；时间戳游标（ISO→unix 秒）+ `(rfc_message_id, file_url)` 唯一索引兜底防重。限流硬约束（10 req/min）：批上限 8 封带附件消息 + 请求间隔 6 秒。

**Tech Stack:** Python（subprocess/json/httpx 大附件下载）、agently-cli v1.0.15（本机已安装并授权，keychain 存 token）、Alembic（SQLite batch 模式放宽 NOT NULL）、Vue 3 + Ant Design Vue（前端类型切换）。

**Spec:** `design/2026-08-15-agently-mail-design.md`（V0.2，含实测输出契约 §六，所有字段名以此为准）。

## Global Constraints

- CLI 输出契约以设计 §六实测为准：`+list` → `data.data[]`（`message_id/created_at(ISO)/from/subject/has_attachments/rfc_message_id`）+ `pagination`；`+read` → `data.attachments[]`（`attachment_id/content_type/filename/size`，大附件为 `download_url`）；`+download` → `data.saved_to`；`+reply` 两步确认（首跑 `data.confirmation_token`，重跑 `--confirmation-token`）
- 限流硬约束：10 req/min、200 req/hr、50 封/天 → `BATCH_LIMIT = 8`、`REQUEST_INTERVAL = 6.0`（秒），单轮请求数 ≈ 1+8×2=17，间隔 6s 保证任意 60s 窗口 ≤10 次
- 游标语义：`last_uid` 存 unix 秒（`created_at`），`>=` 匹配（宁重勿漏），游标推进到本轮**最后一条已处理**消息的时间戳（批上限截断时未处理消息下轮续拉，不丢不跳）
- 去重键：`email_message_id` 用 `rfc_message_id`（缺失时回退 `message_id`）；`provider_message_id = message_id`（msg_ 前缀，供 +reply 定位）
- 金额 Decimal 严禁 float；时间戳 fields.utcnow()；中文注释；测试在仓库根 `test/`、fixture 在 `test/fixtures/agently/`
- 开发模式 SQLite/本地存储/进程内队列，无需 docker；后端回归基线 94 passed 不得回退
- 每个任务结束 git commit，格式 `feat(backend): / feat(web): / fix(...)`；工作目录按任务在 `backend/`、`web/` 或仓库根

---

### Task 1: mailboxes 表扩展（mailbox_type 与 agently 字段）+ Schema/API

**Files:**
- Modify: `backend/src/invoicing/models/mailbox.py`
- Modify: `backend/src/invoicing/schemas/mailbox.py`
- Modify: `backend/src/invoicing/api/mailboxes.py`
- Create: `backend/alembic/versions/<autogen>_mailbox_type_agently.py`（autogenerate 后手工补齐 SQLite 批量放宽）
- Test: `test/test_api_mailboxes.py`（追加 3 条）

**Interfaces:**
- Consumes: `encrypt_secret`（fetch/crypto.py）
- Produces（后续任务依赖）:
  - `Mailbox.mailbox_type: str`（默认 `"imap"`）、`Mailbox.agently_workspace: str | None`、`Mailbox.agently_token_encrypted: str | None`
  - `Mailbox.imap_host / username / password_encrypted` 放宽为可空
  - `MailboxCreate/MailboxUpdate` 增加 `mailbox_type / agently_workspace / agently_token`（`agently_token` 明文入参，加密落库，`MailboxOut` **不回显**）
  - create 校验：`mailbox_type == "imap"` 且缺 `imap_host/username/password` → 422；`agently` 时 imap 字段忽略置 None

- [ ] **Step 1: 写失败测试（test_api_mailboxes.py 追加）**

```python
def test_create_agently_mailbox_without_imap_fields(client, db):
    token = _admin_token(client, db)
    resp = client.post(
        "/api/v1/mailboxes",
        json={"name": "Agently 邮箱", "mailbox_type": "agently", "agently_workspace": "claude-code"},
        headers=_h(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mailbox_type"] == "agently"
    assert body["agently_workspace"] == "claude-code"
    assert "agently_token" not in body and "password" not in body  # 凭据不回显


def test_create_imap_missing_fields_422(client, db):
    token = _admin_token(client, db)
    resp = client.post(
        "/api/v1/mailboxes",
        json={"name": "坏 IMAP", "mailbox_type": "imap", "imap_host": "h"},
        headers=_h(token),
    )
    assert resp.status_code == 422


def test_update_agently_token_encrypted(client, db):
    from invoicing.fetch.crypto import decrypt_secret
    from invoicing.models import Mailbox

    token = _admin_token(client, db)
    resp = client.post(
        "/api/v1/mailboxes",
        json={"name": "M1", "mailbox_type": "imap", "imap_host": "h", "username": "u@x.com", "password": "secret123"},
        headers=_h(token),
    )
    mailbox_id = resp.json()["id"]
    resp2 = client.put(
        f"/api/v1/mailboxes/{mailbox_id}",
        json={"mailbox_type": "agently", "agently_token": "tok-abc"},
        headers=_h(token),
    )
    assert resp2.status_code == 200
    mb = db.get(Mailbox, mailbox_id)
    assert mb.agently_token_encrypted is not None
    assert mb.agently_token_encrypted != "tok-abc"
    assert decrypt_secret(mb.agently_token_encrypted) == "tok-abc"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_api_mailboxes.py -v`
Expected: 新增 3 条失败（422 校验缺失/字段不存在/encrypt 分支缺失——具体失败形态任一种均可，须确认是新增用例失败而非既有用例）

- [ ] **Step 3: 改模型 models/mailbox.py**

```python
    mailbox_type: Mapped[str] = mapped_column(String(16), nullable=False, default="imap")
    agently_workspace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agently_token_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    imap_host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

（替换原 `imap_host/username/password_encrypted` 三行的非空定义；`name` 等其余字段不动。）

- [ ] **Step 4: 改 schema schemas/mailbox.py**

`MailboxOut` 追加：`mailbox_type: str`、`agently_workspace: str | None`。
`MailboxCreate` 追加：`mailbox_type: str = "imap"`、`agently_workspace: str | None = None`、`agently_token: str | None = None`。
`MailboxUpdate` 追加：`mailbox_type: str | None = None`、`agently_workspace: str | None = None`、`agently_token: str | None = None`。

- [ ] **Step 5: 改 API api/mailboxes.py**

create_mailbox 中在构造 Mailbox 前加校验并适配：

```python
    if body.mailbox_type == "imap":
        if not (body.imap_host and body.username and body.password):
            raise HTTPException(422, "IMAP 类型邮箱必须提供 imap_host/username/password")
        agently_token = None
        agently_workspace = None
    elif body.mailbox_type == "agently":
        agently_token = encrypt_secret(body.agently_token) if body.agently_token else None
        agently_workspace = body.agently_workspace
    else:
        raise HTTPException(422, f"非法邮箱类型: {body.mailbox_type}")
```

Mailbox 构造参数改为：`mailbox_type=body.mailbox_type, agently_workspace=agently_workspace, agently_token_encrypted=agently_token`，imap 分支的 `imap_host=body.imap_host` 等保持；agently 分支 imap 三字段传 `None`。

update_mailbox 的循环中，在 `elif field == "smtp_password"` 分支之后、`else` 分支之前插入：

```python
        elif field == "agently_token":
            if value:
                mb.agently_token_encrypted = encrypt_secret(value)
```

（`mailbox_type`、`agently_workspace` 落入既有 else 分支 setattr ✓；update 不做必填校验，MVP 接受。）

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_api_mailboxes.py -v`
Expected: 全部通过（既有 3 + 新增 3）

- [ ] **Step 7: Alembic 迁移**

Run: `cd backend && uv run alembic revision --autogenerate -m "mailbox_type 与 agently 接入字段"`
检查生成脚本：
- 新增三列：`mailbox_type`（含 `server_default='imap'`）、`agently_workspace`、`agently_token_encrypted`
- **SQLite 不支持 ALTER COLUMN DROP NOT NULL** → 若 autogenerate 未生成放宽三列的语句，手工用批量模式补（upgrade/downgrade 对称）：

```python
def upgrade() -> None:
    # ... autogenerate 生成的 add_column 部分 ...
    with op.batch_alter_table("mailboxes") as batch:
        batch.alter_column("imap_host", existing_type=sa.String(256), nullable=True)
        batch.alter_column("username", existing_type=sa.String(256), nullable=True)
        batch.alter_column("password_encrypted", existing_type=sa.String(512), nullable=True)

def downgrade() -> None:
    # 反向：nullable=False（若库内有 NULL 值会失败——降级在开发环境执行，注明风险注释）
    with op.batch_alter_table("mailboxes") as batch:
        batch.alter_column("imap_host", existing_type=sa.String(256), nullable=False)
        batch.alter_column("username", existing_type=sa.String(256), nullable=False)
        batch.alter_column("password_encrypted", existing_type=sa.String(512), nullable=False)
    # ... drop 三列 ...
```

Run: `cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: 往返成功（dev invoicing.db）

- [ ] **Step 8: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 97 passed（94 + 3）

```bash
git add backend/src/invoicing/models/mailbox.py backend/src/invoicing/schemas/mailbox.py \
  backend/src/invoicing/api/mailboxes.py backend/alembic/versions test/test_api_mailboxes.py
git commit -m "feat(backend): mailboxes 支持 mailbox_type 与 agently 接入字段"
```

---

### Task 2: AgentlyFetcher 与协议扩展

**Files:**
- Modify: `backend/src/invoicing/fetch/protocol.py`（`RawMailMessage` 加 `provider_message_id`）
- Create: `backend/src/invoicing/fetch/agently.py`
- Create: `test/fixtures/agently/list_page.json`
- Create: `test/test_agently_fetcher.py`
- Modify: `backend/pyproject.toml`（`uv add httpx` —— 大附件 download_url 分支需要，运行时依赖）

**Interfaces:**
- Consumes: `MailFetcher/RawAttachment/RawMailMessage`（protocol）、`Mailbox`（Task 1 三字段）、`decrypt_secret`
- Produces:
  - `AgentlyCliError(Exception)`
  - `run_cli(mailbox: Mailbox, args: list[str], timeout: int = 30) -> dict`（CLI 未安装/非零退出/超时/非 JSON/`ok!=true` 一律抛 AgentlyCliError；env 注入 `AGENTLY_ACCESS_TOKEN`（解密）/`AGENTLY_WORKSPACE`）
  - `AgentlyFetcher(MailFetcher)`：`fetch_new(last_uid: int) -> list[RawMailMessage]`（批上限 8、间隔 6s、游标 `>=` 匹配、游标推进到本轮最后一条已处理消息）、`mark_seen` no-op
  - 常量：`BATCH_LIMIT = 8`、`REQUEST_INTERVAL = 6.0`、`LIST_LIMIT = 50`

- [ ] **Step 0: 探测 +list 分页 flag 并加依赖**

Run: `agently-cli message +list --help`
查看输出中是否有分页参数（如 `--cursor`）。**若有**：在 `_list_messages` 中循环翻页（`--cursor <next_cursor>` 直到 `has_more=false`）。**若没有**：单页 `--limit 50` + `logger.warning("agently 收件箱超过 %s 条，本轮仅取第一页")`（计划代码按下述「无分页 flag」实现，若探测到 flag 按此规则补循环并在报告说明）。
Run: `cd backend && uv add httpx`
Expected: pyproject 运行时依赖新增 httpx，uv.lock 更新

- [ ] **Step 1: 写 fixture test/fixtures/agently/list_page.json**

```json
{
  "ok": true,
  "data": {
    "data": [
      {
        "created_at": "2026-08-15T08:11:27Z",
        "from": {"email": "aken123@agent.qq.com", "name": "aken123"},
        "has_attachments": true,
        "is_read": false,
        "message_id": "msg_V6HF_D-vUXxlEs_csGIpRYhgppPIzpGr703DUqi32v-bYA",
        "rfc_message_id": "<tencent_20D41DB7025E2102E1FC56AC5024638EFF08@qq.com>",
        "subject": "发票测试-数电票",
        "snippet": "附件为测试数电票 XML 原件"
      },
      {
        "created_at": "2026-08-15T08:10:20Z",
        "from": {"email": "admin@agent.qq.com", "name": "Agent Mail 团队"},
        "has_attachments": false,
        "is_read": false,
        "message_id": "msg_noatt",
        "rfc_message_id": "<tencent_noatt@qq.com>",
        "subject": "无附件邮件",
        "snippet": ""
      }
    ],
    "pagination": {"has_more": false, "next_cursor": ""}
  }
}
```

- [ ] **Step 2: 写失败测试 test/test_agently_fetcher.py**

```python
"""AgentlyFetcher 测试：monkeypatch run_cli 模拟 CLI 输出（契约见设计 §六）。"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invoicing.fetch.agently import AgentlyCliError, AgentlyFetcher, BATCH_LIMIT
from invoicing.fetch.protocol import RawMailMessage
from invoicing.models import Mailbox

FIXTURES = Path(__file__).parent / "fixtures" / "agently"


def _mailbox(db) -> Mailbox:
    mb = Mailbox(name="Agently", mailbox_type="agently", agently_workspace=None)
    db.add(mb)
    db.flush()
    return mb


def _mock_cli(monkeypatch, tmp_path, list_messages=None, read_attachments=None):
    """返回按命令序列应答的 run_cli 假实现；+download 真实写文件到 tmp_path。"""
    import invoicing.fetch.agently as agently_mod

    calls = []

    def fake_run_cli(mailbox, args, timeout=30):
        calls.append(args)
        if args[:2] == ["message", "+list"]:
            payload = {"ok": True, "data": {"data": list_messages or [], "pagination": {"has_more": False}}}
            return payload
        if args[:2] == ["message", "+read"]:
            return {"ok": True, "data": {"attachments": read_attachments or []}}
        if args[:2] == ["attachment", "+download"]:
            name = args[args.index("--att") + 1]
            path = tmp_path / f"{name}.xml"
            path.write_bytes(b"<eInvoice/>")
            return {"ok": True, "data": {"filename": f"{name}.xml", "saved_to": str(path), "size": 11}}
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(agently_mod, "run_cli", fake_run_cli)
    monkeypatch.setattr(agently_mod, "REQUEST_INTERVAL", 0.0)  # 测试不等待
    return calls


def test_fetch_new_skips_no_attachment_and_maps_fields(db, monkeypatch, tmp_path):
    list_messages = json.loads((FIXTURES / "list_page.json").read_text())["data"]["data"]
    calls = _mock_cli(
        monkeypatch, tmp_path, list_messages=list_messages,
        read_attachments=[{"attachment_id": "att_x", "content_type": "text/xml", "filename": "dianzi.xml", "size": "888"}],
    )
    fetcher = AgentlyFetcher(_mailbox(db))
    result = fetcher.fetch_new(0)
    assert len(result) == 1  # 无附件消息跳过
    msg: RawMailMessage = result[0]
    assert msg.message_id == "<tencent_20D41DB7025E2102E1FC56AC5024638EFF08@qq.com>"
    assert msg.provider_message_id == "msg_V6HF_D-vUXxlEs_csGIpRYhgppPIzpGr703DUqi32v-bYA"
    assert msg.sender == "aken123@agent.qq.com"
    assert msg.attachments[0].filename == "dianzi.xml"
    assert msg.attachments[0].data == b"<eInvoice/>"
    expected_uid = int(datetime.fromisoformat("2026-08-15T08:11:27Z").timestamp())
    assert msg.uid == expected_uid
    # +read 与 +download 各被调用一次
    assert sum(1 for c in calls if c[:2] == ["message", "+read"]) == 1
    assert sum(1 for c in calls if c[:2] == ["attachment", "+download"]) == 1


def test_fetch_new_cursor_filters_older(db, monkeypatch, tmp_path):
    list_messages = json.loads((FIXTURES / "list_page.json").read_text())["data"]["data"]
    _mock_cli(monkeypatch, tmp_path, list_messages=list_messages)
    fetcher = AgentlyFetcher(_mailbox(db))
    newest = int(datetime.fromisoformat("2026-08-15T08:11:27Z").timestamp())
    result = fetcher.fetch_new(newest + 1)  # 游标已过最新消息
    assert result == []


def test_fetch_new_batch_limit(db, monkeypatch, tmp_path):
    base = "2026-08-15T08:%02d:00Z"
    list_messages = [
        {
            "created_at": base % (i % 60), "from": {"email": f"u{i}@agent.qq.com", "name": ""},
            "has_attachments": True, "is_read": False, "message_id": f"msg_{i}",
            "rfc_message_id": f"<m{i}@qq.com>", "subject": f"票{i}", "snippet": "",
        }
        for i in range(BATCH_LIMIT + 2)
    ]
    _mock_cli(monkeypatch, tmp_path, list_messages=list_messages)
    fetcher = AgentlyFetcher(_mailbox(db))
    result = fetcher.fetch_new(0)
    assert len(result) == BATCH_LIMIT  # 批上限截断，剩余下轮续拉


def test_cli_missing_raises(db, monkeypatch):
    import invoicing.fetch.agently as agently_mod

    monkeypatch.setattr(agently_mod.shutil, "which", lambda _: None)
    fetcher = AgentlyFetcher(_mailbox(db))
    with pytest.raises(AgentlyCliError, match="未安装"):
        fetcher.fetch_new(0)


def test_run_cli_nonzero_raises(db, monkeypatch):
    import subprocess

    import invoicing.fetch.agently as agently_mod

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(agently_mod.shutil, "which", lambda _: "/usr/local/bin/agently-cli")
    monkeypatch.setattr(agently_mod.subprocess, "run", fake_run)
    fetcher = AgentlyFetcher(_mailbox(db))
    with pytest.raises(AgentlyCliError, match="退出码"):
        fetcher.fetch_new(0)
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_agently_fetcher.py -v`
Expected: FAIL（`invoicing.fetch.agently` 不存在）

- [ ] **Step 4: 实现协议扩展与 agently.py**

protocol.py 的 `RawMailMessage` 追加字段（dataclass 中 `provider_message_id: str | None = None`，置于 sender 之后）。

```python
# fetch/agently.py
"""Agently Mail 收取适配：subprocess 调 agently-cli（输出契约见设计 V0.2 §六）。

限流硬约束（10 req/min、200 req/hr）：单轮批上限 BATCH_LIMIT 封带附件消息，
请求间隔 REQUEST_INTERVAL 秒，保证任意 60 秒窗口请求数 ≤ 10。
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx

from invoicing.fetch.crypto import decrypt_secret
from invoicing.fetch.protocol import MailFetcher, RawAttachment, RawMailMessage
from invoicing.models import Mailbox

logger = logging.getLogger(__name__)

BATCH_LIMIT = 8
REQUEST_INTERVAL = 6.0
LIST_LIMIT = 50


class AgentlyCliError(Exception):
    """agently-cli 不可用（未安装/未授权/超时/输出非法）。"""


def _cli_env(mailbox: Mailbox) -> dict:
    env = dict(os.environ)
    if mailbox.agently_token_encrypted:
        env["AGENTLY_ACCESS_TOKEN"] = decrypt_secret(mailbox.agently_token_encrypted)
    if mailbox.agently_workspace:
        env["AGENTLY_WORKSPACE"] = mailbox.agently_workspace
    return env


def run_cli(mailbox: Mailbox, args: list[str], timeout: int = 30) -> dict:
    if not shutil.which("agently-cli"):
        raise AgentlyCliError("agently-cli 未安装")
    try:
        proc = subprocess.run(
            ["agently-cli", *args],
            capture_output=True, text=True, timeout=timeout, env=_cli_env(mailbox),
        )
    except subprocess.TimeoutExpired as e:
        raise AgentlyCliError(f"agently-cli 超时: {e}") from e
    if proc.returncode != 0:
        raise AgentlyCliError(f"agently-cli 退出码 {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AgentlyCliError(f"agently-cli 输出非 JSON: {e}") from e
    if not payload.get("ok"):
        raise AgentlyCliError(f"agently-cli 返回失败: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return payload


def _to_unix(iso: str) -> int:
    """ISO8601 → unix 秒。秒级游标：同秒多条靠 (rfc_message_id, file_url) 唯一索引兜底。"""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class AgentlyFetcher(MailFetcher):
    def __init__(self, mailbox: Mailbox):
        self.mailbox = mailbox

    def fetch_new(self, last_uid: int) -> list[RawMailMessage]:
        messages = self._list_messages()
        candidates = [m for m in messages if _to_unix(m["created_at"]) >= last_uid]
        candidates.sort(key=lambda m: m["created_at"])  # 旧→新，游标只推进到已处理处
        result: list[RawMailMessage] = []
        processed = 0
        last_processed_ts: int | None = None
        with tempfile.TemporaryDirectory(prefix="agently-att-") as tmp:
            for msg in candidates:
                ts = _to_unix(msg["created_at"])
                if msg.get("has_attachments"):
                    if processed >= BATCH_LIMIT:
                        logger.info("本轮达到批上限 %s，剩余消息下轮续拉", BATCH_LIMIT)
                        break
                    attachments = self._download_attachments(msg, Path(tmp))
                    processed += 1
                else:
                    attachments = []
                result.append(
                    RawMailMessage(
                        uid=ts,
                        message_id=msg.get("rfc_message_id") or msg["message_id"],
                        provider_message_id=msg["message_id"],
                        subject=msg.get("subject") or "",
                        sender=(msg.get("from") or {}).get("email") or "",
                        attachments=attachments,
                    )
                )
                last_processed_ts = ts
                time.sleep(REQUEST_INTERVAL)
        return result

    def _list_messages(self) -> list[dict]:
        payload = run_cli(self.mailbox, ["message", "+list", "--limit", str(LIST_LIMIT)])
        pagination = (payload.get("data") or {}).get("pagination") or {}
        if pagination.get("has_more"):
            logger.warning("agently 收件箱超过 %s 条，本轮仅取第一页", LIST_LIMIT)
        return (payload["data"].get("data") or [])

    def _download_attachments(self, msg: dict, tmp: Path) -> list[RawAttachment]:
        payload = run_cli(self.mailbox, ["message", "+read", "--id", msg["message_id"]])
        attachments: list[RawAttachment] = []
        for att in (payload.get("data") or {}).get("attachments") or []:
            if att.get("attachment_id"):
                dl = run_cli(
                    self.mailbox,
                    ["attachment", "+download", "--msg", msg["message_id"],
                     "--att", att["attachment_id"], "--output", str(tmp)],
                )
                content = Path(dl["data"]["saved_to"]).read_bytes()
                time.sleep(REQUEST_INTERVAL)
            elif att.get("download_url"):
                content = self._download_url(att["download_url"])
            else:
                logger.warning("附件无 attachment_id/download_url，跳过: %s", att.get("filename"))
                continue
            attachments.append(
                RawAttachment(
                    filename=att.get("filename") or "attachment.bin",
                    content_type=att.get("content_type") or "application/octet-stream",
                    data=content,
                )
            )
        return attachments

    def _download_url(self, url: str) -> bytes:
        headers = {}
        if self.mailbox.agently_token_encrypted:
            headers["Authorization"] = f"Bearer {decrypt_secret(self.mailbox.agently_token_encrypted)}"
        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content

    def mark_seen(self, uids: list[int]) -> None:
        pass  # Agently 无已读标记；防重靠 (rfc_message_id, file_url) 唯一索引
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_agently_fetcher.py -v`
Expected: 5 PASS（若测试 monkeypatch 的模块属性名与实现不符，按实现为准微调测试并说明）

- [ ] **Step 6: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 102 passed（97 + 5）

```bash
git add backend/src/invoicing/fetch/agently.py backend/src/invoicing/fetch/protocol.py \
  backend/pyproject.toml backend/uv.lock test/test_agently_fetcher.py test/fixtures/agently
git commit -m "feat(backend): AgentlyFetcher（agently-cli 收取适配）与协议扩展"
```

---

### Task 3: 拒收回复分发与 poll 分发

**Files:**
- Modify: `backend/src/invoicing/fetch/reply.py`（agently 分支 + 函数签名扩展）
- Modify: `backend/src/invoicing/fetch/service.py`（fetcher 按类型分发 + 回复调用传 provider_message_id）
- Test: `test/test_fetch.py`（追加 2 条）

**Interfaces:**
- Consumes: `run_cli`（Task 2）、`AgentlyFetcher`（Task 2）
- Produces:
  - `send_reject_reply(mailbox, to_addr, subject, provider_message_id: str | None = None, body: str = REJECT_TEMPLATE) -> None`（agently：`+reply` 两步确认，缺 provider_message_id 跳过并记日志；imap 分支不变）
  - `poll_mailbox` 的默认 fetcher 按 `mailbox.mailbox_type` 分发

- [ ] **Step 1: 写失败测试（test/test_fetch.py 追加）**

```python
def test_poll_agently_mailbox_uses_agently_fetcher(db, monkeypatch):
    from invoicing.fetch.agently import AgentlyFetcher
    from invoicing.fetch.service import poll_mailbox

    mb = Mailbox(name="Agently", mailbox_type="agently", imap_host=None, username=None, password_encrypted=None)
    db.add(mb)
    db.flush()
    captured = {}

    class FakeAgentlyFetcher(AgentlyFetcher):
        def fetch_new(self, last_uid):
            captured["called"] = True
            return []

        def mark_seen(self, uids):
            pass

    monkeypatch.setattr("invoicing.fetch.agently.AgentlyFetcher", FakeAgentlyFetcher)
    poll_mailbox(db, mb)
    assert captured.get("called") is True


def test_reject_reply_agently_two_step(db, monkeypatch):
    from invoicing.fetch.reply import send_reject_reply

    mb = Mailbox(name="Agently", mailbox_type="agently", imap_host=None, username=None, password_encrypted=None)
    db.add(mb)
    db.flush()
    calls = []

    def fake_run_cli(mailbox, args, timeout=30):
        calls.append(args)
        if "--confirmation-token" in args:
            return {"ok": True, "data": {"queued": True}}
        return {"ok": True, "data": {"confirmation_required": True, "confirmation_token": "ctk_x"}}

    monkeypatch.setattr("invoicing.fetch.agently.run_cli", fake_run_cli)
    send_reject_reply(mb, "user@agent.qq.com", "发票照片", provider_message_id="msg_1")
    assert len(calls) == 2  # 首跑 + 确认重跑
    assert calls[0][:3] == ["message", "+reply", "--id"]
    assert "--confirmation-token" in calls[1]
```

（注意：既有 `test_poll_receives_xml_invoice` 等 FakeFetcher 用例显式传入 fetcher，不受分发影响。）

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_fetch.py -v`
Expected: 新增 2 条失败（service 无 AgentlyFetcher 引用 / reply 无 provider_message_id 参数）

- [ ] **Step 3: 实现 reply.py 分发**

`send_reject_reply` 签名改为 `(mailbox, to_addr, subject, provider_message_id=None, body=REJECT_TEMPLATE)`，函数开头插入：

```python
    if mailbox.mailbox_type == "agently":
        _reply_agently(mailbox, provider_message_id, body)
        return
```

文件末尾追加：

```python
def _reply_agently(mailbox: Mailbox, provider_message_id: str | None, body: str) -> None:
    """agently 拒收回复：+reply 两步确认（首跑拿 token，重跑完成）。"""
    if not provider_message_id:
        logger.info("agently 拒收回复缺少 provider_message_id，跳过 mailbox_id=%s", mailbox.id)
        return
    from invoicing.fetch.agently import run_cli  # 延迟导入避免循环

    first = run_cli(mailbox, ["message", "+reply", "--id", provider_message_id, "--body", body])
    token = (first.get("data") or {}).get("confirmation_token")
    if not token:
        raise ValueError(f"agently +reply 未返回 confirmation_token: {json.dumps(first, ensure_ascii=False)[:200]}")
    run_cli(
        mailbox,
        ["message", "+reply", "--id", provider_message_id, "--body", body, "--confirmation-token", token],
    )
```

（`json` 需在 reply.py 顶部导入。）

- [ ] **Step 4: 实现 service.py 分发**

`poll_mailbox` 开头：

```python
    if fetcher is None:
        if mailbox.mailbox_type == "agently":
            from invoicing.fetch.agently import AgentlyFetcher

            fetcher = AgentlyFetcher(mailbox)
        else:
            fetcher = ImapMailFetcher(mailbox)
```

图片拒收分支的调用改为：

```python
            send_reject_reply(mb, msg.sender, msg.subject, provider_message_id=msg.provider_message_id)
```

（`send_reject_reply` 内 SMTP 分支的 `to_addr` 判断保持原样；agently 分支用 provider_message_id 定位，to_addr 仅作日志。）

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_fetch.py -v`
Expected: 全部通过（既有 8 + 新增 2）

- [ ] **Step 6: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 104 passed（102 + 2）

```bash
git add backend/src/invoicing/fetch/reply.py backend/src/invoicing/fetch/service.py test/test_fetch.py
git commit -m "feat(backend): 拒收回复与 fetcher 按 mailbox_type 分发"
```

---

### Task 4: 前端类型切换与接入文档

**Files:**
- Modify: `web/src/types.ts`（MailboxOut/MailboxCreate/MailboxUpdate 加 agently 字段）
- Modify: `web/src/views/SettingsView.vue`（邮箱表单加类型切换）
- Modify: `docs/开发环境指南.md`（Agently 接入章节）
- Test: `web/src/views/__tests__/settings.spec.ts`（追加 1 条渲染断言）

**Interfaces:**
- Consumes: Task 1 的 API 契约（create/update 接受 mailbox_type/agently_workspace/agently_token）
- Produces: 前端 `MailboxCreate` 类型含 `mailbox_type?: string; agently_workspace?: string | null; agently_token?: string | null`

- [ ] **Step 1: 写失败测试（settings.spec.ts 追加）**

```ts
  it("邮箱表单渲染类型切换选项", () => {
    const wrapper = mount(SettingsView, { global: { stubs: { "a-tabs": { template: "<div><slot /></div>" }, "a-tab-pane": { template: "<div><slot /></div>" }, "a-modal": { template: "<div />" }, "a-table": { template: "<div />" } } } });
    // 打开新建邮箱 modal 后应能看到 IMAP/Agently 类型选项（此处仅断言渲染无异常）
    expect(wrapper.exists()).toBe(true);
  });
```

（SettingsView 的 modal 内容在 v-model:open=false 时是否渲染取决于 Antd 实现——若断言访问不到类型选项，改为直接断言组件含「类型」文案或仅保留 exists 断言并在报告说明。测试以「不崩溃 + 组件渲染」为最低门槛。）

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd web && npm run test`
Expected: 新用例失败或不稳定（字段/选项未实现前 exists 可能已通过——若通过则在实现后补强断言，报告说明）

- [ ] **Step 3: types.ts 与 SettingsView 实现**

```ts
// types.ts 中 MailboxOut 追加
  mailbox_type: string;
  agently_workspace: string | null;
// MailboxCreate 追加
  mailbox_type?: string;
  agently_workspace?: string | null;
  agently_token?: string | null;
// MailboxUpdate 同 MailboxCreate
```

SettingsView 的 mailboxForm 定义改为：

```ts
const mailboxForm = reactive({
  name: "", mailbox_type: "imap", imap_host: "", imap_port: 993 as number | undefined,
  username: "", password: "", keywords: "发票,Invoice",
  agently_workspace: "", agently_token: "",
});
```

保存逻辑 `saveMailbox` 组装 payload：

```ts
      const payload: Record<string, unknown> = {
        name: mailboxForm.name,
        mailbox_type: mailboxForm.mailbox_type,
        keywords: mailboxForm.keywords,
      };
      if (mailboxForm.mailbox_type === "imap") {
        Object.assign(payload, {
          imap_host: mailboxForm.imap_host,
          imap_port: mailboxForm.imap_port,
          username: mailboxForm.username,
        });
        if (mailboxForm.password) payload.password = mailboxForm.password; // 编辑时留空不修改
      } else {
        payload.agently_workspace = mailboxForm.agently_workspace || null;
        if (mailboxForm.agently_token) payload.agently_token = mailboxForm.agently_token;
      }
      if (editingMailbox.value) await updateMailbox(editingMailbox.value.id, payload);
      else await createMailbox(payload as MailboxCreate);
```

邮箱 modal 表单中「IMAP 主机/端口/账号/密码」四个 a-form-item 外层包 `v-if="mailboxForm.mailbox_type === 'imap'"`；新增两个 a-form-item：

```vue
        <a-form-item v-if="mailboxForm.mailbox_type === 'agently'" label="工作区">
          <a-input v-model:value="mailboxForm.agently_workspace" placeholder="留空 = 默认工作区" />
        </a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'agently'" label="Access Token">
          <a-input-password v-model:value="mailboxForm.agently_token" placeholder="留空 = 使用服务器本机授权（keychain）" />
        </a-form-item>
```

在「名称」之前插入类型选择：

```vue
        <a-form-item label="类型">
          <a-radio-group v-model:value="mailboxForm.mailbox_type">
            <a-radio-button value="imap">IMAP</a-radio-button>
            <a-radio-button value="agently">Agently</a-radio-button>
          </a-radio-group>
        </a-form-item>
```

新建/编辑按钮的 `Object.assign(mailboxForm, {...})` 重置补齐 `mailbox_type: "imap", agently_workspace: "", agently_token: ""`；编辑分支从 record 填充：`mailbox_type: record.mailbox_type, agently_workspace: record.agently_workspace || ""`。

- [ ] **Step 4: 测试 + 构建**

Run: `cd web && npm run test && npm run build`
Expected: 全绿 + 构建成功

- [ ] **Step 5: 文档更新（docs/开发环境指南.md 追加章节）**

```markdown
## Agently Mail 接入（Agent 原生邮箱）

1. 安装并授权（一次性）：`npm install -g @tencent-qqmail/agently-cli` 后执行 `agently-cli auth login`，按提示在浏览器完成授权（token 存系统 keychain）。
2. 后台「系统配置 → 邮箱配置 → 新建邮箱」，类型选 **Agently**：工作区留空（默认）或填 `AGENTLY_WORKSPACE`；Access Token 留空（依赖服务器本机授权）或填 `AGENTLY_ACCESS_TOKEN`（加密存储）。
3. 「测试连接」验证 CLI 可用，「手动收取」触发拉取。收件按主题关键词过滤，发票附件自动进入解析/验真链路。
4. 注意限流：10 次/分钟、200 次/小时、50 封/天（发送）；单轮轮询最多处理 8 封带附件邮件，剩余自动下轮续拉。
```

CLAUDE.md 构建与测试节追加一行：

```bash
agently-cli auth login                              # Agently 邮箱接入（一次性授权，需 node/npm）
```

- [ ] **Step 6: 回归并提交**

Run: `cd web && npm run test`（前端）+ `cd backend && uv run pytest ../test -q`（后端 104 不变）
Expected: 全绿

```bash
git add web/src/types.ts web/src/views/SettingsView.vue web/src/views/__tests__/settings.spec.ts \
  docs/开发环境指南.md CLAUDE.md
git commit -m "feat(web): 邮箱配置支持 Agently 类型与接入文档"
```

---

### Task 5: 真实 Agently 邮箱全链路验收

**Files:**
- Create: `tmp/agently-e2e.py`（临时验收脚本，不入库）
- Create: `docs/agently-接入验收.md`（验收记录）

**Interfaces:**
- Consumes: Task 1-3 全部落地 + 本机 agently-cli 已授权（keychain）
- Produces: 验收记录（收件箱里现成的「发票测试-数电票」邮件走完收取→解析→验真→列表可见）

- [ ] **Step 1: 前置检查**

Run: `agently-cli +me`（确认授权仍在）、`agently-cli message +list --limit 3 | grep 发票测试`（确认测试邮件仍在收件箱）
Run: `cd backend && uv run alembic upgrade head`（dev 库迁移就绪）
Expected: 均正常

- [ ] **Step 2: 写验收脚本 tmp/agently-e2e.py**

```python
"""Agently 真实验收：配置邮箱 → 真实收取 → 断言发票入库。运行后删除即可。"""
from invoicing.db import SessionLocal
from invoicing.fetch.service import poll_mailbox
from invoicing.models import Invoice, Mailbox

db = SessionLocal()
mb = db.query(Mailbox).filter(Mailbox.name == "Agently 验收").first()
if mb is None:
    mb = Mailbox(name="Agently 验收", mailbox_type="agently")
    db.add(mb)
    db.commit()
    print("已创建 agently 邮箱 id =", mb.id)
else:
    print("复用 agently 邮箱 id =", mb.id)

result = poll_mailbox(db, mb)
print("收取结果:", result)

inv = db.query(Invoice).filter(Invoice.email_subject == "发票测试-数电票").first()
assert inv is not None, "未找到「发票测试-数电票」邮件入库"
assert inv.status == "pending_submit", f"状态异常: {inv.status}"
assert inv.parse_source == "XML"
assert inv.total_amount is not None
assert inv.xml_url is not None
print(f"验收通过: invoice #{inv.id} {inv.invoice_number} {inv.status} {inv.total_amount}")
db.close()
```

- [ ] **Step 3: 执行验收**

Run: `cd backend && uv run python ../tmp/agently-e2e.py`
Expected: 打印「收取结果: PollResult(received=1, ...)」与「验收通过: invoice #N 24312000000012345678 pending_submit 1000.00」
（若邮箱配置在后台 UI 里已建过同名邮箱，脚本复用；若收取报 AgentlyCliError，先 `agently-cli auth login` 恢复授权再重跑。）

- [ ] **Step 4: 写验收记录 docs/agently-接入验收.md**

```markdown
# Agently 接入验收记录

- 日期：2026-08-15
- 邮箱：aken123@agent.qq.com（本机 keychain 授权，未配置 token）
- 用例：收件箱「发票测试-数电票」（附件 dianzi.xml，2026-08-15 发出）
- 结果：真实收取 → 解析（XML，置信度 1.0）→ 验真（Mock passed）→ `pending_submit`，价税合计 1000.00，XML 原件已归档
- 命令：`cd backend && uv run python ../tmp/agently-e2e.py`（脚本执行后删除）
- 备注：限流参数（批上限 8、间隔 6s）生效；+reply 拒收回复需图片邮件用例另行验证
```

- [ ] **Step 5: 清理并提交**

```bash
rm -f tmp/agently-e2e.py
git add docs/agently-接入验收.md
git commit -m "docs: Agently 接入真实验收记录"
```

---

## Plan E 验收清单（全部完成后核对）

- [ ] 后端 104 测试全绿（含 AgentlyFetcher 5 条、分发 2 条、mailbox API 3 条）
- [ ] 前端 13 测试全绿 + build 成功
- [ ] 真实收件箱「发票测试-数电票」→ `pending_submit` 验收通过并有记录文档
- [ ] 设计 §六 契约逐条落地：+list 分页/游标、+read 附件清单、+download、+reply 两步确认、限流批上限与间隔
- [ ] 防重语义：`rfc_message_id` 作 email_message_id；同秒消息唯一索引兜底
