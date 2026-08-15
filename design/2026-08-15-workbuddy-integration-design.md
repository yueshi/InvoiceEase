# 发票易 × WorkBuddy 集成设计（V0.1 草案，待评审）

| 项目 | 内容 |
|------|------|
| 文档版本 | V0.1 |
| 编制日期 | 2026-08-15 |
| 状态 | 待评审 |
| 输入 | `/Users/james/WorkBuddy/2026-08-15-19-40-33/invoice-extractor-mcp/design.html`（WorkBuddy 侧生成的设计方案） |
| 现状事实 | ① 发票易已有 MCP Server（/mcp，streamable-http + 静态 token，3 工具）② 已有解析/校验引擎（XML/OFD/PDF 内嵌 XBRL；价税合计/大小写校验）③ 已有 Agently 邮箱接入（`claude` 工作区已授权，aken123@agent.qq.com） |

---

## 一、关键事实认定

1. **MCP 服务器之间不能互调**（design.html 核心约束）——Agent Mail（WorkBuddy 平台 Connector）与发票易 MCP 是两个独立工具源，只能由 Agent 编排，通过**本地文件系统**间接协作。
2. **WorkBuddy 绑定的 Agent Email 不经过 agently-cli**——本机 `~/.agently-cli/agents/` 只有 `claude` 工作区（我们已授权）；WorkBuddy 的 Agent Mail 是平台管理式 Connector（`SearchMessages / ListAttachments / download_attachment` 等工具），邮箱凭据由 WorkBuddy 平台持有，发票易**不可也不应**直接触及。
3. 结论：**「使用 WorkBuddy 绑定的 Agent Email」= 由 Agent 编排**：Agent Mail 搜索/下载附件到本地目录 → 发票易 MCP 读取本地文件完成识别与归档。

## 二、集成模式（两种，互补）

### 模式 A：Agent 编排（本设计的落地主体）

```
WorkBuddy Agent
 ├─ Agent Mail Connector（平台）：SearchMessages → ListAttachments → download_attachment
 │     → 附件落盘 /Users/james/WorkBuddy/invoices/xxx.pdf
 ├─ 发票易 MCP（自定义，streamable-http /mcp + token）：读本地文件
 │     ├─ extract_invoice(file_path)        → 识别（不入库）
 │     ├─ batch_extract_invoices(paths[])   → 批量识别
 │     ├─ validate_invoice(invoice_data)    → 数据校验
 │     └─ invoice_ingest(file_path)         → 识别+入库+验真（闭环归档）
 └─ Agent 汇总输出（表格/报告）
```

### 模式 B：服务端直连（现有能力，仅补文档）

发票易 Web/MCP 的 `invoice_fetch` 轮询 agently 邮箱（`claude` 工作区）——这是**另一个邮箱体系**（发票易自管），与 WorkBuddy 绑定的 Agent Email 无关。适合企业自建收票邮箱场景，与模式 A 并存。

## 三、新增 MCP 工具（在既有 /mcp server 上扩展）

| 工具名 | 输入 | 输出 | 说明 |
|--------|------|------|------|
| `extract_invoice` | `file_path: string` | `ExtractResult` | 单文件识别（XML/OFD/PDF）；**图片按合规拒收**（FRD G-03：仅收原件） |
| `batch_extract_invoices` | `file_paths: string[]` | `ExtractResult[]` | 批量识别，逐条 success/error 互不影响 |
| `validate_invoice` | `invoice_data: dict` | `ValidationResult` | 复用校验引擎（价税合计/大小写金额） |
| `invoice_ingest` | `file_path: string` | `InvoiceOut` | 原件入对象存储 → 解析 → 验真（本地模式内联）→ 返回发票记录；审计 channel=mcp |

**命名映射**：design.html 的 `extract_invoice / batch_extract_invoices / validate_invoice` 命名保留（Skill 指令可直接引用）；`invoice_ingest` 为发票易补充的归档闭环工具。

### ExtractResult 结构（对齐 design.html §3.2，缺失字段置空）

```json
{
  "success": true,
  "error": null,
  "data": {
    "invoiceType": "增值税电子普通发票",   // 由 EInvoiceType 映射；未知置 null
    "invoiceNumber": "24312000000012345678",
    "invoiceCode": null,                   // 数电票无代码
    "issueDate": "2026-08-01",
    "checkCode": null,                     // 数电票 XML 无此字段
    "buyer": {"name": "…", "taxId": "…", "addressPhone": null, "bankAccount": null},
    "seller": {"name": "…", "taxId": "…", "addressPhone": null, "bankAccount": null},
    "amountWithoutTax": "909.09",
    "taxAmount": "90.91",
    "totalWithTax": "1000.00",
    "totalWithTaxCN": "壹仟元整",
    "items": [],                           // 明细解析 Phase 2（OCR 引擎补充）
    "sourceFile": "/Users/.../dianzi.xml",
    "extractedAt": "2026-08-15T12:00:00Z"
  },
  "validation": {"valid": true, "errors": [], "warnings": []}
}
```

错误语义（`success=false` + `error` 文案）：
- 图片输入 → `"合规拒收：仅接受 PDF/OFD/XML 原件（财会〔2025〕9 号）"`
- 纯版式 PDF（无内嵌结构化数据）→ `"未内嵌结构化数据，OCR 引擎 Phase 2 支持"`
- 解析失败 → 解析错误详情

`validate_invoice`：输入按 ExtractResult.data 字段名解析 → 映射到内部 ParsedInvoice → 复用 `parse/validation.py`（TOTAL_MISMATCH/CN_MISMATCH → errors；warnings 恒空，预留）。

`invoice_ingest`：分类 → 原件存 `storage`（key：`tenant-default/workbuddy/{文件名}`）→ 建 `Invoice(status=parsing, file_type, email_subject="WorkBuddy 导入", email_message_id=None)` → 内联 `_parse_invoice` + `_verify_invoice` → 返回 InvoiceOut；查重由唯一索引兜底（重复文件报 blocked 语义）。

## 四、WorkBuddy 侧配置

`~/.workbuddy/mcp.json`（与既有 mcpServers 合并）：

```json
{
  "mcpServers": {
    "invoice-ease": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
    }
  }
}
```

（SDK v2 客户端经 httpx2 AsyncClient 携带 Bearer 头——Plan B 已实测该路径；WorkBuddy 自定义 MCP 配置格式以其实际支持为准，启用时按平台 UI 校正。）

**Skill 编排**（`~/.workbuddy/skills/invoice-auto-collect/SKILL.md`）：

```
1. mcp__agent-mail__SearchMessages(q="发票", has_attachments=true)  → 候选邮件
2. mcp__agent-mail__ListAttachments(message_id)                     → 附件清单
3. mcp__agent-mail__download_attachment(...)                        → 落盘 /Users/james/WorkBuddy/invoices/
4. mcp__invoice-ease__batch_extract_invoices(file_paths)            → 批量识别
5. mcp__invoice-ease__invoice_ingest(file_path)（合规原件）          → 入库归档
6. 汇总输出（表格/报告/自然语言）
```

## 五、职责边界（对齐 design.html §6）

| 能力 | Agent Mail（平台） | 发票易 MCP |
|------|------|------|
| 搜索/读取/下载邮件 | ✓ | — |
| 发票文字提取与结构化 | — | ✓（XML/XBRL 优先） |
| 数据校验 | — | ✓ |
| 验真/查重/归档 | — | ✓（ingest） |
| 发信/回复 | ✓ | —（拒收回复走服务端模式 B） |

## 六、实施任务草案（Plan F，评审后细化）

| # | 任务 | 内容 |
|---|------|------|
| F1 | extract/validate 工具 | 本地文件分类读取、ExtractResult 映射、validate 映射、6+ 条测试（XML/OFD/PDF/图片拒收/批量/校验） |
| F2 | invoice_ingest 工具 | 入库全链路（含查重拦截语义）、测试 |
| F3 | WorkBuddy 集成文档与 Skill | mcp.json 示例、SKILL.md、docs 章节、真机联调说明 |

## 七、风险与决策点

1. **WorkBuddy 自定义 MCP 的配置格式**（streamable-http 是否原生支持/字段名）——启用时以平台 UI 实测为准，可能需要 stdio 适配器（若平台只支持 stdio：加一个 `mcp-stdio-adapter` 薄进程转发到 /mcp，Phase 2 备选）。
2. **图片拒收与 design.html 的 OCR 路线冲突**——FRD 合规优先：extract 对图片返回合规拒收文案；OCR 路线留给 Phase 2 的「模糊件兜底」统一规划（需用户确认图片是否允许提取）。
3. 提取工具的字段缺口（checkCode/明细项/购销方地址银行）——Phase 2 OCR 引擎补；当前置 null，Skill 输出时明确标注。
