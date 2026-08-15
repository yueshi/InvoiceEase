# invoice-auto-collect

自动收集 WorkBuddy Agent Email 中的发票邮件，识别并归档到发票易。

## 工作流

1. `mcp__agent-mail__SearchMessages(q="发票", has_attachments=true)` → 候选邮件列表
2. 逐封 `mcp__agent-mail__ListAttachments(message_id)` → 附件清单（只看 PDF/OFD/XML 附件）
3. `mcp__agent-mail__download_attachment(...)` 下载到 `/Users/james/WorkBuddy/invoices/`
4. `mcp__invoice-ease__batch_extract_invoices(file_paths)` → 批量识别（图片附件跳过并提示用户走原件邮箱）
5. 识别成功的原件执行 `mcp__invoice-ease__invoice_ingest(file_path)` → 入库归档（返回待提交/已拦截状态）
6. 汇总输出：表格列出 发票号码/销售方/价税合计/状态；拦截的注明原因（重复发票）

## 约束

- 仅处理 PDF/OFD/XML 附件；JPG/PNG 等图片按合规要求拒绝识别
- 同一发票不要重复 ingest（系统按发票代码+号码自动拦截）
- 邮件与附件由 Agent Mail Connector 管理，本 Skill 不实现任何邮件协议
