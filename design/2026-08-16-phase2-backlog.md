# Phase 2 待办清单（增强版 Roadmap 输入）

> 来源：MVP（Plan A-F）实施与真机测试中发现的功能缺口与增强项。
> 状态：持续更新。执行 Phase 2 规划时以此为输入。

## 解析引擎

1. **出行/服务类电子发票 XML 变体解析**（真机测试发现，2026-08-15）：高德打车电子发票 XML 结构与数电票 XML 不同（缺 `EInvoiceNumber/IssueDate` 节点），当前 `xml_parser` 只认数电票 schema → 解析失败进待复核。需要：调研各类电子发票 XML schema（出行/餐饮/服务），扩展解析器字段映射（保留原件合规不变）。
2. **OCR + 大模型引擎**（FRD Phase 2 核心）：纯版式 PDF / 模糊件走 OCR+LLM 兜底，接入分级路由的 `PDF_UNSTRUCTURED` 路径。含字段缺口补全：checkCode、明细项 items[]、购销方地址电话/开户行账号。
3. 图片识别决策：FRD 合规「只收原件」与 design.html 的 OCR 图片路线冲突——按 FRD 合规优先执行（extract 工具已对图片返回拒收文案）；是否开放图片 OCR 需产品确认。

## Agently / 邮件

4. `+reply --confirmed` 参数实测（固定模板跳过两步确认）。
5. 大附件 `download_url` 分支真实验收（当前走 attachment_id 路径）。
6. 分页内 `created_at` 缺失守卫（终审 re-review 发现的 minor）。
7. 进程内限流计数器的多 worker/多进程部署语义（Plan D 时解决）。

## MCP / WorkBuddy

8. WorkBuddy 自定义 MCP 配置格式已实测可用（streamable-http + headers，2026-08-15 ✓）——关闭设计 §七风险 1。
9. stdio 适配器备选（若其他平台仅支持 stdio）。
10. `_mcp_admin_user` id=0 哨兵：Phase 2 写工具（verify/submit/stats）接入前必须解决（审计外键语义）。
11. SKILL.md 的 agent-mail 工具名大小写与平台实测对齐。

## 部署 / 生产对齐

12. 见 `design/2026-08-15-plan-d-checklist.md`（redis 模式实测、received 状态叙事、PG timestamptz、S3 流式、zip bomb 等）。

## 业务功能（FRD Phase 2）

13. 多邮箱支持、批量提交（金蝶/用友 API）、RBAC 完整实现、报表导出、WorkBuddy 深度集成（6 Tool + 自然语言）、OCR 引擎（= 第 2 条）。
