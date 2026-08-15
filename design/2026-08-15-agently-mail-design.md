# 发票易 × Agently Mail 接入设计（V0.1 草案，待评审）

| 项目 | 内容 |
|------|------|
| 文档版本 | V0.2 |
| 编制日期 | 2026-08-15 |
| 状态 | 待评审（输出契约已实测校准） |
| 需求基线 | FRD G-08（P1）：支持 Agently Mail 等 Agent 专用邮箱服务接入 |
| 事实来源 | https://agent.qq.com/doc/cli-setup.md + npm readme + **本机实测**（`agently-cli` v1.0.15，已授权，2026-08-15） |

---

## 一、背景与事实认定

**Agently Mail 是「Agent 原生邮箱」，没有 IMAP/SMTP 协议**。访问全部通过 `agently-cli` 命令（Node 实现），OAuth 授权，凭据按 Agent 工作区隔离。

已确认的 CLI 能力（v1.0.15）：

| 能力 | 命令 |
|------|------|
| 当前身份 | `agently-cli +me` |
| 邮件列表 | `agently-cli message +list --limit N` |
| 读单封 | `agently-cli message +read --id msg_xxx` |
| 搜索 | `agently-cli message +search --q "..."` |
| 发信/回复 | `message +send` / `+reply`（**两步确认**：首跑返回 confirmation_token，重跑带 `--confirmation-token` 才真正执行） |
| 附件下载 | `agently-cli attachment +download --msg msg_xxx --att att_xxx --output ./dir` |
| 附件上传 | `agently-cli attachment +upload --file ./x` |

认证：`agently-cli auth login`（交互式浏览器 OAuth）；无头场景用环境变量 `AGENTLY_ACCESS_TOKEN`（最高优先级）、`AGENTLY_WORKSPACE`（工作区）、`AGENTLY_CLI_CONFIG_DIR`。

**关键未知项（需授权后探测，见 §六）**：`+list`/`+read` 的 JSON 输出结构、附件 id 是否随消息返回、消息时间戳字段、`+send/+reply` 的确认 token 格式。

---

## 二、架构设计

**总体思路：把 Agently 当作 MailFetcher 协议的第二个实现，全链路复用。**

```
poll_mailbox(db, mailbox)
   └─ fetcher = 按 mailbox.mailbox_type 分发
        ├─ imap    → ImapMailFetcher（现状，零改动）
        └─ agently → AgentlyFetcher（新增，subprocess 调 agently-cli）
   └─ 附件过滤/ZIP/拒收回复/入库/入队 parse→verify：全部复用现状
```

### 新增组件

**`fetch/agently.py` — AgentlyFetcher(MailFetcher)**：
- `fetch_new(last_uid)`：
  1. 检查 `shutil.which("agently-cli")`，缺失抛 `AgentlyCliError`
  2. 构造环境变量（token 解密后注入 `AGENTLY_ACCESS_TOKEN`；workspace 注入 `AGENTLY_WORKSPACE`）
  3. 执行 `agently-cli message +list --limit 50`，解析 JSON
  4. 过滤时间游标：消息时间戳 > last_uid（见 §三 uid 语义）
  5. 对每条含附件的消息：`attachment +download` 逐个下载到临时目录 → 读字节 → 组装 `RawAttachment`
  6. 返回 `RawMailMessage(uid=时间戳, provider_message_id=msg_xxx, subject, sender, attachments)`
  7. finally 清理临时目录
- `mark_seen(uids)`：**no-op**（Agently 无已读标记概念；防重靠 §三 三重防线）
- 错误处理：CLI 未安装/未授权/超时（subprocess timeout=30s）/JSON 解析失败 → 抛 `AgentlyCliError`，由 `poll_mailbox` 既有异常兜底捕获（审计 + errors+1），与 IMAP 认证失败同待遇

**协议扩展（`fetch/protocol.py`）**：`RawMailMessage` 增加 `provider_message_id: str | None = None`——拒收回复需要定位原邮件（agently 的 +reply 需要 msg id；IMAP 场景留空）。

**拒收回复分发（`fetch/reply.py`）**：`send_reject_reply` 按 `mailbox.mailbox_type` 分发：
- imap → SMTP（现状）
- agently → `agently-cli message +reply --id <provider_message_id> --body <拒收模板>`，处理两步确认（首跑解析 confirmation_token，重跑携带；失败记审计）

**fetcher 分发（`fetch/service.py`）**：`poll_mailbox` 默认 fetcher 从「固定 ImapMailFetcher」改为按类型：
```python
fetcher = fetcher or (ImapMailFetcher(mailbox) if mailbox.mailbox_type == "imap" else AgentlyFetcher(mailbox))
```

---

## 三、防重复收取（Agently 无 \Seen / 无 uid）

现有三重防线适配：
1. `\Seen` 标记 → **不适用**（mark_seen no-op）
2. `last_uid` 游标 → **语义改为时间戳游标**：`+list` 返回的消息含时间字段（待探测确认字段名），`uid = 时间戳`（秒级整数）；`last_uid` 存最近成功处理消息的时间戳。风险：同一秒多条消息、时间戳缺失——降级方案：`uid = 消息稳定序号的负数` 不可靠，改用「拉最近 N 条 + 唯一索引兜底」（第 3 重防线承受全部去重压力，代价是多下载几次附件，MVP 可接受）
3. `(email_message_id, file_url)` 唯一索引 → **主力防线**：重复消息被 `_store_original` 的 IntegrityError 分支拦截（已有测试）

设计决定：**游标用「探测到的时间戳字段」，缺字段或同秒多条时依赖第 3 重防线兜底**——不追求增量精确，保证不丢不漏（宁重勿漏）。

---

## 四、数据模型变更（Alembic 迁移）

`mailboxes` 表：

| 字段 | 变更 | 说明 |
|------|------|------|
| `mailbox_type` | **新增** String(16) NOT NULL default `'imap'` | `imap` \| `agently` |
| `agently_workspace` | **新增** String(64) NULL | AGENTLY_WORKSPACE；空 = 默认工作区 |
| `agently_token_encrypted` | **新增** String(512) NULL | Fernet 加密的 AGENTLY_ACCESS_TOKEN；空 = 依赖本机 CLI 已登录态 |
| `imap_host` / `username` / `password_encrypted` | **改为 NULL** | agently 类型不需要；API 层校验：type=imap 时必填 |

API/Schema 扩展：`MailboxCreate/MailboxUpdate/MailboxOut` 增加三字段；create/update 校验「imap 类型必填 imap 字段，agently 类型忽略之」。

前端（SettingsView 邮箱表单）：类型选择（IMAP/Agently），按类型切换表单字段。**注意**：前端表单此前无 `:model` 的登录 bug 同源风险——Agently 表单必须绑 `:model`。

---

## 五、核心流程（agently 邮箱一次轮询）

```
调度器/手动收取 → poll_mailbox(mailbox_type=agently)
→ AgentlyFetcher.fetch_new(last_uid=时间戳游标)
   → agently-cli message +list --limit 50（JSON）
   → 过滤时间戳 > 游标
   → 逐条: 主题关键词匹配（复用 _subject_matches）
       ├─ 有附件 → attachment +download 逐个 → RawAttachment
       └─ 无附件 → 忽略
→ 现有 _process_attachment 分类（PDF/OFD/XML/ZIP/图片拒收）
→ 图片 → send_reject_reply（agently +reply 两步确认）
→ 入库（email_message_id + file_url 唯一索引兜底重复）
→ last_uid = max(时间戳)（宁重勿漏：即使部分失败也推进游标，失败已审计）
→ 入队 parse→verify（本地模式内联）——全部复用
```

---

## 六、实测输出契约（2026-08-15 授权后实测，已校准设计）

**认证**：`auth login` 交互式 OAuth（token 存系统 keychain，macOS）；服务器部署用 `AGENTLY_ACCESS_TOKEN` 环境变量（readme 声明最高优先级，未实测——部署前验证）。本机工作区凭据已可用（邮箱 `aken123@agent.qq.com`）。

**+me**：`{ok, data: {aliases: [{alias_id, email, is_primary, name}], constraints: {max_attachment_count: 50, max_attachment_size_bytes: 20971520（20MB）, max_total_attachments_size_bytes: 20971520}, rate_limits: {daily_send_quota: 50, requests_per_hour: 200, requests_per_minute: 10}, scopes}}`

**+list**（无需 --format，stdout 即 JSON）：`{ok, data: {data: [{created_at: ISO8601, from: {email, name}, has_attachments: bool, is_read, message_id: "msg_...", snippet, subject, to: [...]}], pagination: {has_more, next_cursor, previous_cursor}}}` —— **分页有 next_cursor**，超过 limit 需循环拉取。

**+read**：`{ok, data: {attachments: [{attachment_id: "att_...", content_type, filename, size} | {download_url（大附件时无 attachment_id）, ...}], attachment_count, body, body_format, created_at, from, has_attachments, message_id, rfc_message_id: "<tencent_...@qq.com>", subject, to/cc/bcc}}` —— **附件清单在 +read 而非 +list**；**rfc_message_id 可直接作 email_message_id 去重键**。

**+download**：`{ok, data: {filename, saved_to: 绝对路径, size}}` —— 实测下载字节与源文件一致。

**+send**：`--attachment <相对路径>`（≤10MB/个、总额 20MB、50 个）；两步确认：首跑 `{confirmation_required: true, confirmation_token, summary}`，`--confirmation-token` 完成；**`--confirmed` 跳过确认**（固定模板场景可用）。实测发送成功 `{queued: true}`。

**+reply**：两步确认同 +send：首跑返回 `confirmation_token`，重跑 `--confirmation-token <ctk_...>` 完成。

**限流约束（设计必守）**：10 req/min、200 req/hr、每日发送 50 封。默认 5 分钟一轮轮询安全；但**带附件消息每封需要 +read（1 次）+ 每附件 +download（1 次）**，一封 3 附件的邮件 = 4 次请求，忙碌收件箱一轮 50 封可达 200+ 请求撞小时限——AgentlyFetcher 需配额意识：单轮批上限（如 20 封）、遇 429/限流退避（指数退避 + 下轮续拉），审计记录。

**对设计的校准结论**：
1. 时间戳游标成立：`created_at` ISO8601 → 转 unix 秒存 `last_uid`；同秒多条靠 `(email_message_id=rfc_message_id, file_url)` 唯一索引兜底（宁重勿漏）✓
2. fetch_new 流程修订：`+list`（分页循环）→ 对 `has_attachments=true` 的每条 `+read` 取 attachments[] → 逐个 `+download` 到临时目录 → 组装 RawAttachment；大附件（download_url）分支：HTTP GET 下载（带 Authorization 头，token 与 CLI 同源）
3. provider_message_id = `message_id`（msg_ 前缀，供 +reply 定位）；email_message_id = `rfc_message_id`
4. 拒收回复：+reply 两步确认——固定模板场景尝试 `--confirmed`（若 +reply 支持，探测未验证该 flag；不支持则解析 confirmation_token 重跑）

---

## 七、实施任务草案（探测完成后细化为 Plan E）

| # | 任务 | 内容 |
|---|------|------|
| E1 | 迁移 + 模型/Schema/API | mailbox_type 三字段、NULL 放宽、API 校验、测试 |
| E2 | AgentlyFetcher + 协议扩展 | fetch/agently.py、provider_message_id、subprocess 封装（mock CLI fixture 测试）、游标语义 |
| E3 | 回复分发 + 调度分发 | reply.py 按类型分发（两步确认）、poll_mailbox 分发、测试 |
| E4 | 前端表单 + 文档 | SettingsView 类型切换（绑 :model）、docs 接入说明 |
| E5 | 真实授权验收 | 探测（§六）→ 校准 → 真实邮箱全链路冒烟 |

**验收标准**：agently 邮箱收取→解析→验真→列表可见全链路 e2e（mock CLI fixture）+ 真实授权后手工冒烟通过；重复邮件不重复入库；拒收回复真实送达。

---

## 八、风险与决策点（待评审）

1. **CLI 输出契约未定**——一切解析器依赖探测结果；设计已按「JSON 输出 + 时间戳字段 + 附件 id 内嵌」假设起草，探测后校准。**风险最高项。**
2. **运行时依赖**：部署机需 node + agently-cli + 一次性 OAuth 授权（或长期 token）——与「纯离线部署」兼容（CLI 本身联网调用腾讯服务，属外部依赖，需在部署文档明示）。
3. **token 长期有效性**未知——若过期，收取静默失败（审计可查）；是否需要「授权过期」告警面（Web 红点）作为后续增强。
4. **两步确认的 +reply**：若 confirmation_token 有有效期/一次性语义，拒收回复的异步重试需谨慎；探测确认。
5. mailbox_type 是枚举扩展点——未来第三方邮箱（如 Gmail API）同样按此模式接入。
