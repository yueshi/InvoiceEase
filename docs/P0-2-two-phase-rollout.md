# P0-2 两段握手——上线与运维手册

> 基线：`frd/Agent-Digital-Employee-Requirements-v1.1.md` §7.5 / §7.5.1
> 实施方案：`design/2026-10-10-P0-2-two-phase-writes-执行计划.md`
> 生效范围：MCP Agent 通道的 **20 个写工具**

## 1. 变更摘要

| 变更 | 内容 |
|---|---|
| 写工具两段化 | 20 个写 MCP 工具改名 `*_proposal`（返回 `proposal_token`，**不落库**） |
| 新增确认入口 | `confirm_execute(token, tool_name, human_ack=True, idempotency_key=?)` —— 唯一落库入口 |
| 幂等 | 同 `idempotency_key` 24h 内重放返回首次结果（proposal 与 confirm 命名空间隔离） |
| 有效期 | `proposal_token` 15 分钟、一次性、绑定发起主体 |
| 前端二次确认 | 银行账号新增/删除、大额（≥ 阈值）提交/审批 → 高风险弹窗（必填理由） |
| REST 通道 | **不变**（spec §7.5：Web 人工通道免两段握手） |

## 2. 调用方（Agent / 客户端）迁移

```
旧：expense_create(title="差旅")                       → 直接落库
新：expense_create_proposal(title="差旅")              → {"proposal_token", "preview", ...}
    用户确认后
    confirm_execute(token=<proposal_token>,
                    tool_name="expense_create",
                    human_ack=true)                    → 落库，返回与旧接口一致的结果
```

- `human_ack=false` 一律拒绝（服务端强制，LLM 无法代为确认）
- 跨主体确认拒绝（proposal 绑定发起人）
- 网络重试：两次调用带同一 `idempotency_key` 只执行一次

## 3. 监控指标

| 指标 | 查询 | 期望 |
|---|---|---|
| 确认成功率 | 审计 `action LIKE '%_CONFIRMED'` 计数 | 与 `*_proposal` 调用量同量级 |
| 待确认堆积 | `SELECT COUNT(*) FROM proposals WHERE consumed_at IS NULL AND expires_at > now()` | 无持续增长 |
| 阻断量 | 审计 `action='SUBMIT_BLOCKED'` | 反映 validate_expense 拦截 |
| 幂等命中 | `SELECT COUNT(*) FROM idempotency_keys` 增速 | 与重试率一致 |

## 4. 已知限制与豁免

| 项 | 说明 |
|---|---|
| **回滚机制** | 无运行时开关。回滚 = `git revert` 本批提交后重启（旧直调路径已删除，开关切不动，故不设死开关） |
| **scope 校验时机** | scope/role 在**提案阶段**把关；确认阶段只校验「token 有效 + 主体一致 + 工具一致」。**令牌被撤权后 15 分钟内已签发的提案仍可确认** —— 撤权需配合吊销令牌（verifier 每次请求重查，令牌吊销即时生效） |
| **幂等键归属** | 幂等命名空间 = 工具 + **主体** + 阶段；跨用户同 key 不互相命中 |
| `invoice_ingest` / `invoice_fetch` / `extract_invoice` | **豁免**：属收取管线入口（与邮件自动采集耦合），不在 20 个两段式工具内（plan 裁决，见 ledger） |
| 文件类工具 | `receipt_ingest` / `sales_invoice_import` 的 payload 只存路径，确认时才读文件 —— 提案与确认间隔内文件须可读，否则确认报错（路径内容未绑定哈希，属已知 TOCTOU，见 ledger） |
| Web 通道 | 走前端 ConfirmModal（§7.5.1），不消费 `proposal_token`（spec 明示避免双重确认）；弹窗必填的**理由**随 REST 请求落审计 `detail.note`（v1.1 §7.2 ✅4） |
| `budget:read` scope | 未新增；预算/校验工具复用 `expense:read` |
| 提案堆积 | `proposals` 表暂无清理任务（过期行保留）；如需清理按 `expires_at < now() - 7d` 定期删除 |

## 5. 排障

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `human_ack=true required` | 客户端漏传 `human_ack` | 检查调用方；这是设计行为 |
| `proposal expired` | 超过 15 分钟未确认 | 重新发起提案 |
| `proposal already consumed` | 同 token 二次确认 | 正常拒绝；重试请带 `idempotency_key` |
| `proposal 归属主体不符` | 他人 token 确认 | 安全拒绝，核查令牌归属 |
| `tool not registered for two-phase` | 调用未注册工具名 | 用 `my_permissions` 查可用清单 |
| 确认后无变化 | 幂等命中（同 key 已执行过） | 查 `idempotency_keys` 表 |
