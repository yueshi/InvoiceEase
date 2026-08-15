# 发票易 × Agently Mail 接入设计（V0.1 草案，待评审）

| 项目 | 内容 |
|------|------|
| 文档版本 | V0.1 |
| 编制日期 | 2026-08-15 |
| 状态 | 待评审 |
| 需求基线 | FRD G-08（P1）：支持 Agently Mail 等 Agent 专用邮箱服务接入 |
| 事实来源 | https://agent.qq.com/doc/cli-setup.md + npm `@tencent-qqmail/agently-cli` v1.0.15 readme（2026-08-15 获取） |

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

## 六、授权后探测计划（授权完成后执行，回填本设计）

1. `agently-cli +me` → 输出结构与邮箱地址
2. `agently-cli message +list --limit 5` → JSON 结构：消息字段（id/时间/主题/发件人）、附件 id 是否内嵌
3. `agently-cli message +read --id X` → 是否需要（附件清单是否在 +list 里）
4. `attachment +download` 真实下载一枚附件 → 文件落盘行为
5. `message +reply` 两步确认的实际输出格式（confirmation_token 位置）
6. `AGENTLY_ACCESS_TOKEN` 注入后命令是否免交互（验证无头可用性）
7. CLI 输出是否稳定 JSON（有无 `--format json` 参数）

探测结果将校准 §二/§三 的具体字段名与解析器。

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
