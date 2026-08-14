# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

发票易（InvoiceEase）—— 面向企业级市场的电子发票自动化处理 AI Agent 产品，实现电子发票从「邮箱收取 → 智能解析 → 验真查重 → 归档提交」的全流程自动化，通过 MCP 协议接入 WorkBuddy 等 Agent 平台。

**当前处于需求阶段：仓库内仅有需求文档，尚无任何代码。**

## 关键文档

- `frd/InvoiceEase-frd-v0.1.txt` —— 产品需求文档（PRD V1.0），当前唯一的权威基线。任何实现工作前必须先通读；需求歧义或冲突时以该文档为准。

## 产品架构（来自 FRD，实现时以此为蓝本）

五个核心模块：

1. **发票获取**：IMAP 定时轮询企业邮箱，抓取附件。仅接受 PDF/OFD/XML 原件，图片格式自动拒收并回复提示邮件；ZIP 自动解压；已处理邮件需标记避免重复收取。
2. **发票解析（分级路由）**：XML 数电发票直接解析 > 内嵌 XBRL 的 OFD/PDF 提取结构化数据 > 纯版式 PDF 走 OCR+大模型 > 模糊件走多模态大模型（Qwen-VL）兜底。提取字段遵循财政部电子凭证会计数据标准；校验逻辑自洽（价税合计=不含税+税额）、大小写金额一致；置信度 < 0.8 需人工复核。
3. **验真与查重**：调用国税查验平台 API 验真；以「发票代码+发票号码」为唯一键全库查重，重复直接拦截。
4. **WorkBuddy 集成（MCP Server）**：全部能力封装为 MCP Tools：`invoice_fetch` / `invoice_list` / `invoice_detail` / `invoice_verify` / `invoice_submit` / `invoice_stats`。
5. **Web 管理后台**：多租户 RBAC 分角色看板（普通员工/财务专员/财务主管/系统管理员），含工作台、发票列表、人工复核、批量提交、报表导出、系统配置、审计日志。

**发票状态机**：`收取中 → 已收取 → 解析中 → 解析完成 → 验真查重中 → 待提交/待复核/已拦截 → 已提交 → 已归档`

**数据模型核心**：`invoices` 主表（含 `tenant_id`、解析来源、置信度、验真/查重标记、状态、原件存储路径）+ `audit_logs` 操作日志表。

**合规硬约束**（影响所有实现决策）：
- 严格遵循财会〔2025〕9 号文；只收原件，拍照/截图一律拒收
- 无论是否打印纸质，必须保存含数字签名的 XML 原件
- 验真覆盖率 100%，审计日志完整可追溯
- 支持纯离线部署（数据不出企业内网）

## 技术选型（FRD 推荐方案，尚未落地）

| 组件 | 方案 |
|------|------|
| 后端 | Python 3.11+ / FastAPI |
| MCP | Python MCP SDK |
| 数据库 | PostgreSQL（结构化数据）、Redis（会话/队列） |
| 对象存储 | MinIO / 阿里云 OSS（发票原件） |
| 前端 | React / Vue 3 + Ant Design |
| 部署 | Docker Compose / Kubernetes |

## 构建与测试

尚未有代码，因此不存在构建、lint、测试命令。开始实现时按 FRD 第五章技术选型搭建工程，并将实际可用的命令补充到本节。

## 工作约定

- 遵循用户全局约定的目录规范：设计方案存 `design/`、工具用法文档存 `docs/`、测试代码存 `test/`、临时内容存 `tmp/`（当前 FRD 位于 `frd/`，为已有布局）
- 大块实现前先在 `design/` 产出设计并确认，再分步实施；Roadmap 分 MVP / 增强版 / 企业版三阶段（见 FRD 第六章），实现时按阶段推进
