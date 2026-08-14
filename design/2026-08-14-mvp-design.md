# 发票易（InvoiceEase）MVP 技术设计

| 项目 | 内容 |
|------|------|
| 文档版本 | V1.0 |
| 编制日期 | 2026-08-14 |
| 状态 | 已评审确认（2026-08-14） |
| 需求基线 | `frd/InvoiceEase-frd-v0.1.txt`（PRD V1.0） |
| 对应阶段 | Roadmap Phase 1：MVP |

---

## 一、范围与目标

依据 FRD 第六章 Phase 1 裁剪，MVP 交付以下能力，其余明确不做（留 Phase 2/3）：

| 模块 | MVP 范围 | 明确不做 |
|------|---------|---------|
| 发票获取 | 单邮箱 IMAP 轮询、主题关键词筛选、PDF/OFD/XML 过滤、图片拒收+SMTP 回复、ZIP 解压、已处理标记 | 多邮箱并发监控、公网链接抓取（G-05） |
| 发票解析 | XML 直接解析 + OFD/PDF 内嵌 XBRL 提取；纯版式 PDF → 标记待复核 | OCR、大模型引擎（Phase 2） |
| 验真查重 | 查重必做（代码+号码唯一键）；验真走 VerifyProvider 抽象 + Mock 实现 | 真实服务商对接（Phase 2+） |
| Web 后台 V1 | 登录、发票列表/详情/基础筛选、人工复核、工作台统计、审计日志查看、邮箱/用户配置 | 报表导出、批量提交、审批流（Phase 2） |
| MCP V1 | `invoice_fetch` / `invoice_list` / `invoice_detail` | 其余 3 个 Tool（Phase 2） |
| 部署 | Docker Compose 单机、支持纯离线 | 多租户、SSO、K8s（Phase 3） |

### 已确认的关键决策（评审记录）

1. **整体形态**：单体 FastAPI 应用（方案 A），模块边界用包结构保证，不拆微服务
2. **验真**：统一 `VerifyProvider` 接口 + Mock 实现（规则引擎可配置），真实服务商适配器后续插拔接入
3. **前端**：Vue 3 + Ant Design Vue
4. **登录**：轻量 users 表 + 登录（JWT），**4 角色完整建齐**（员工/财务专员/财务主管/系统管理员），完整 RBAC 权限矩阵 Phase 2 细化
5. 单租户部署（`tenant_id` 预留字段，默认 `'default'`），多租户 Phase 3

---

## 二、整体架构与模块划分

**进程形态**：单代码库、双进程。`app` 容器跑 FastAPI（REST + MCP 挂载 + APScheduler 定时轮询），`worker` 容器跑 arq（基于 Redis 的 asyncio 任务队列）消费解析/验真任务。同镜像、同配置，Docker Compose 里两个 service。

**包结构**（模块边界对应 FRD 5.1 分层）：

```
backend/
  app/
    main.py          # FastAPI 装配：lifespan 启动 MCP session_manager + APScheduler
    config.py        # pydantic-settings 统一配置
    api/             # REST：auth / invoices / mailboxes / users / stats / audit
    mcp/             # MCP Server 与 3 个 MVP Tool 的实现
    fetch/           # IMAP 客户端、附件过滤、ZIP 解压、拒收回复(SMTP)
    parse/           # 分级解析：xml_parser / ofd_parser(xbrl) / schemas / validation
    verify/          # VerifyProvider 抽象 + MockVerifyProvider + 查重服务
    workflow/        # 状态机 + 任务编排（入队、结果落库）
    storage/         # MinIO 原件存储封装
    models/          # SQLAlchemy ORM 模型
    db/              # 会话管理 + Alembic 迁移
    workers/         # arq 任务函数（parse_invoice / verify_invoice）
web/                 # Vue 3 + Ant Design Vue 前端
deploy/              # docker-compose.yml / Dockerfile / nginx.conf / .env.example / backup.sh
test/                # 测试代码与样例 fixture（遵循项目目录约定）
```

**依赖规则**：`api/` 与 `mcp/` 是表现层，只调用 `workflow` 暴露的 service；`fetch/parse/verify/storage` 各引擎互不直接依赖，由 `workflow` 编排；`models/db` 为底层公共依赖。Phase 2/3 拆分进程时只需改 `workflow` 的编排方式，引擎不动。

**已定技术点**：

- MCP：官方 Python MCP SDK v2，`streamable_http_app()` 挂载到 FastAPI 的 `/mcp` 路径；宿主 lifespan 必须运行 `mcp.session_manager.run()`（SDK v2 挂载模式的要求）
- 定时轮询：APScheduler（AsyncIOScheduler，单机够用），默认 5 分钟可配置
- 任务队列：arq（asyncio 原生 Redis 队列）
- 状态机：内存枚举 + `workflow/state.py` 转换表，不引入状态机框架

---

## 三、数据模型（PostgreSQL）

### 表结构（5 张表，Alembic 管理迁移）

**users** — 轻量用户表（4 角色）
- `id`、`username`(unique)、`password_hash`（bcrypt）、`role`、`created_at`、`updated_at`
- `role` 枚举：`employee`（普通员工）/ `finance_staff`（财务专员）/ `finance_manager`（财务主管）/ `admin`（系统管理员）
- 权限实现：代码内权限矩阵常量表（`role → {actions}`），FastAPI 依赖注入做接口级校验

**mailboxes** — 邮箱配置（MVP 单条，表结构支持多条）
- `id`、`name`、`imap_host`、`imap_port`、`use_ssl`、`username`、`password`（Fernet 加密存储）、`folder`（默认 INBOX）、`keywords`（主题关键词，逗号分隔）、`poll_interval_seconds`（默认 300）
- SMTP 回信配置：`smtp_host`、`smtp_port`、`smtp_username`、`smtp_password`
- `enabled`、`last_polled_at`、`last_uid`（已处理位置）
- `created_at`、`updated_at`

**invoices** — 发票主表
- 标识/归属：`id`、`tenant_id`(default `'default'`)、`mailbox_id`、`email_message_id`、`email_subject`
- 发票字段：`invoice_code`、`invoice_number`、`issue_date`、`amount_without_tax`、`tax_amount`、`total_amount`、`total_amount_cn`、`seller_name`、`seller_tax_id`、`buyer_name`、`buyer_tax_id`、`invoice_type`
- 金额一律 `NUMERIC(14,2)`；时间为 `timestamptz`
- 解析：`parse_source`（`XML` / `OFD_XBRL` / `PDF_XBRL` / `PDF_UNSTRUCTURED`）、`confidence_score`、`validation_errors`(JSONB)
- 验真：`verify_status`（`pending` / `passed` / `failed`）、`verify_detail`(JSONB)、`verified_at`
- 查重：`duplicate_flag`、`duplicate_of_id`
- 状态与复核：`status`（状态机枚举）、`review_note`、`reviewed_by`、`reviewed_at`
- 原件：`file_url`（MinIO object key）、`file_type`（`PDF`/`OFD`/`XML`）、`xml_url`（**合规硬约束**：数电票 XML 原件单独存储，即使原件为 PDF/OFD，内嵌 XML 也需抽取另存）
- `created_at`、`updated_at`

**audit_logs** — 审计日志
- `id`、`user_id`、`action`（`FETCH`/`PARSE`/`VERIFY`/`REVIEW`/`LOGIN`/`REJECT_REPLY`/`CONFIG_CHANGE` 等）、`invoice_id`(nullable)、`detail`(JSONB，含 `channel`：`web`/`mcp`/`system`)、`ip_address`、`created_at`

### 关键约束与索引

- 查重唯一键：`UNIQUE INDEX ON invoices (tenant_id, COALESCE(invoice_code,''), invoice_number)` —— 数电票 20 位号码无代码时 COALESCE 兜底，数据库级拦截并发重复
- 防重复收取：`email_message_id` 部分唯一索引（`WHERE email_message_id IS NOT NULL`）
- 查询索引：`invoices(status)`、`invoices(created_at DESC)`、`invoices(issue_date)`、`audit_logs(created_at)`、`audit_logs(user_id)`

### 状态机（FRD 枚举完整落地，转换集中于 `workflow/state.py`）

```
收取中 → 已收取 → 解析中 → 解析完成 → 验真查重中 → 待提交
                                     ├─ 校验失败/低置信度 → 待复核 → (人工) → 待提交 / 驳回
                                     └─ 重复发票 → 已拦截（记录 duplicate_of_id）
```

- 状态枚举：`receiving / received / parsing / parsed / verifying / pending_submit / pending_review / blocked / rejected / submitted / archived`
- `rejected`（人工驳回）为 MVP 可到达的终态；`submitted`、`archived` 在代码中定义完整，但 MVP 无批量提交功能，流程自然终止在 `pending_submit`，Phase 2 接上
- 任何非法转换直接抛异常；转换表为纯数据，单元测试全覆盖

### 设计说明

- 不建任务表：arq 自带任务队列与重试，任务结果直接写 `invoices` 状态
- 邮件"已处理"靠 IMAP `\Seen` 标记 + `mailboxes.last_uid` + `email_message_id` 唯一索引三重防线，不落邮件表

---

## 四、核心流程与错误处理

### 流程一：收取（APScheduler，默认每 5 分钟）

```
IMAP 登录(单邮箱) → 搜索未读邮件 → 主题关键词筛选(发票/Invoice)
→ 附件分类：
   ├─ PDF/OFD/XML → 原件上传 MinIO → 建 invoices(status=received) → 入队解析任务
   ├─ ZIP → 解压 → 其中 PDF/OFD/XML 同上；解压后仍无合规附件 → 忽略并记审计日志
   ├─ JPG/PNG 等图片 → 不落库，SMTP 回复拒收提示 + 记审计日志
   └─ 非发票附件 → 忽略 + 记审计日志
→ 邮件标记 \Seen + 更新 mailboxes.last_uid
```

- 认证失败/网络异常：重试 3 次后停本轮，记审计日志并置告警状态（Web 后台红点提示）

### 流程二：解析（arq worker 消费）

```
XML → 直接解析数电票节点 → parse_source=XML
OFD/PDF → 尝试提取内嵌 XBRL（财政部电子凭证会计数据标准）→ parse_source=OFD_XBRL/PDF_XBRL
两者都拿不到结构化数据 → status=pending_review，parse_source=PDF_UNSTRUCTURED，记审计日志
（Phase 2 OCR 引擎接入后此路径自动升级为 OCR+LLM）
```

- 校验逻辑：价税合计 = 不含税金额 + 税额；大写金额与小写金额一致。任一不通过 → `validation_errors` 记录 → 走「待复核」而非直接进验真
- 任务失败：arq 重试 2 次（指数退避），仍失败 → `status=pending_review` + 审计日志（发票绝不丢，最坏进人工队列）
- XML 原件合规：无论原收到格式，内嵌/伴随 XML 均单独抽取存 MinIO 并记 `xml_url`

### 流程三：验真查重（arq worker 消费）

```
查重先行：唯一键 (tenant_id, code, number) 冲突 → duplicate_flag=true,
  duplicate_of_id=先到发票, status=blocked（唯一索引兜底并发）
验真：VerifyProvider.verify(invoice) → MockVerifyProvider 规则引擎
  （按发票号码规则模拟通过/失败/异常，规则可配置）
→ passed → status=pending_submit；failed → status=pending_review + verify_detail 记原因
```

- `VerifyProvider` 接口契约：`verify(invoice) -> VerifyResult(status, detail, raw)`；真实服务商适配器（百望/航信）实现同名接口、改配置切换，核心流程零改动
- 验真异常（超时/抖动）：重试 2 次后落 `failed` 进人工复核，**不自动放行**——保证「验真覆盖率 100%」不被绕过

### 全链路审计

所有状态跃迁写 `audit_logs`（action + 操作人 + invoice_id + detail JSONB + channel），Web 后台审计日志页（admin 可见），MCP 触发的操作同样留痕。

---

## 五、接口定义

### REST API（前缀 `/api/v1`，统一错误格式 `{code, message, detail}`）

| 分组 | 端点 | 说明 | 权限 |
|------|------|------|------|
| 认证 | `POST /auth/login` | 返回 JWT（HS256，8h 过期） | 公开 |
| 认证 | `POST /auth/logout` / `GET /auth/me` | 登出/当前用户 | 登录 |
| 发票 | `GET /invoices` | 列表：`status/date_from/date_to/keyword/page` 筛选 | 员工仅本人，财务+全公司 |
| 发票 | `GET /invoices/{id}` | 详情（结构化字段+原件链接） | 同上 |
| 发票 | `GET /invoices/{id}/file` | 原件下载流（`file`/`xml` 参数选原件/XML） | 同上 |
| 发票 | `POST /invoices/{id}/review` | 人工复核 `{action: approve\|reject, note}` | 专员+ |
| 发票 | `POST /invoices/{id}/verify` | 手动重新验真 | 专员+ |
| 邮箱 | `GET/POST/PUT /mailboxes` | 邮箱配置管理 | admin |
| 邮箱 | `POST /mailboxes/{id}/poll` | 手动触发收取 | admin |
| 邮箱 | `POST /mailboxes/{id}/test` | 测试 IMAP/SMTP 连通性 | admin |
| 用户 | `GET/POST/PUT /users` | 轻量用户管理（4 角色） | admin |
| 统计 | `GET /stats/overview` | 工作台：待复核数/待提交数/今日新增/本月累计 | 登录 |
| 审计 | `GET /audit-logs` | 按操作人/动作/时间筛选 | admin |

### MCP Tools（MVP 3 个）

| Tool | 输入 | 输出 |
|------|------|------|
| `invoice_fetch` | `mailbox_id`（可选，缺省全部启用邮箱） | 收取结果统计（收到/拒收/忽略各几张） |
| `invoice_list` | `status, date_from, date_to, page, page_size` | 发票列表（分页） |
| `invoice_detail` | `invoice_id` | 完整发票信息（结构化字段+状态+验真结果） |

**关键设计点**：

1. REST 与 MCP 共享同一 service 层——MCP Tool 是薄适配器，直接调用 `workflow` service，两套入口行为一致
2. MCP Server 挂载 `/mcp`（streamable-http），与 REST 同进程不同路径
3. MCP 鉴权：MVP 用静态 Bearer token（配置项 `MCP_TOKEN`），内网部署够用；Phase 2 升级 OAuth2.0
4. MCP 触发的操作写审计日志（`detail.channel=mcp`）
5. 员工权限隔离（仅本人发票）在 service 层统一过滤，避免 API 层漏过滤

---

## 六、部署形态与配置

### Docker Compose 拓扑（单机、纯离线可用）

| Service | 职责 | 端口（内网） |
|---------|------|------|
| `web` | Nginx：Vue 构建产物 + 反代到 app | 80 |
| `app` | FastAPI（REST `/api` + MCP `/mcp`）+ APScheduler 轮询 | 8000（仅 web 可达） |
| `worker` | arq 消费解析/验真任务（同镜像，不同启动命令） | 无 |
| `postgres` | 结构化数据（volume 持久化） | 仅内网 |
| `redis` | 任务队列 | 仅内网 |
| `minio` | 发票原件/XML 对象存储（volume 持久化） | 9000（后台访问） |

一条 `docker compose up -d` 拉起全部。

### 配置管理

- pydantic-settings 统一加载：`.env` 文件 + 环境变量覆盖
- 敏感项全部走环境变量：`DATABASE_URL`、`REDIS_URL`、`MINIO_SECRET_KEY`、`JWT_SECRET`、`MCP_TOKEN`、`FERNET_KEY`（邮箱密码加密）、`MAILBOX_*`
- 镜像预装全部依赖（无运行时拉包），前端产物打进 `web` 镜像——离线部署零外部依赖；唯一外联是内网 IMAP/SMTP 服务器

### 备份与运维（MVP 基础版）

- `deploy/backup.sh`：`pg_dump` + MinIO 数据目录快照，crontab 定时
- 日志：app/worker 结构化 JSON 日志到 stdout，`docker logs` 收集

---

## 七、测试策略（test/ 目录）

| 层级 | 范围 | 工具 |
|------|------|------|
| 单元测试 | 解析引擎（真实 XML/OFD/PDF 样例 fixture）、校验规则（价税合计/大小写）、状态机转换表（含非法转换）、查重唯一键 | pytest |
| 集成测试 | API 全链路：登录 → 列表 → 复核；arq 任务消费与重试 | pytest + 测试库 |
| 提供方测试 | MockVerifyProvider 规则引擎、VerifyProvider 接口契约（供 Phase 2 适配器遵循） | pytest |
| 前端测试 | 关键页面冒烟（列表/详情/复核表单） | Vitest + Vue Test Utils |

- 测试样例：`test/fixtures/` 存放脱敏的真实格式 XML（数电票）、OFD（内嵌 XBRL）、纯版式 PDF 各若干
- 不追求覆盖率数字，但核心链路「收取→解析→验真→列表可见」必须有自动化测试兜底

---

## 八、与 Phase 2/3 的衔接点

| 衔接点 | MVP 处理 | 后续升级 |
|--------|---------|---------|
| 验真 | VerifyProvider 抽象 + Mock | 百望/航信适配器插拔，改配置切换 |
| 解析 | 结构化优先；纯版式 PDF → 待复核 | OCR+LLM 引擎接入同一分级路由 |
| 权限 | 4 角色建齐 + 权限矩阵常量 | 完整 RBAC 界面化配置、SSO |
| 员工归属 | 发票来自统一收票邮箱，员工视图为空 | 员工转发邮件按发件人自动归属 `user_id` |
| MCP | 3 个 Tool + 静态 token | 6 个 Tool 全量 + OAuth2.0 |
| 多租户 | 单租户（`tenant_id='default'`） | 多租户隔离（Phase 3） |
| 部署 | Docker Compose 单机 | Kubernetes（Phase 3） |
