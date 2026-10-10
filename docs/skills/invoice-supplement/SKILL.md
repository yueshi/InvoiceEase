---
name: invoice-supplement
description: "Supplemental invoice filing: ingest a newly received invoice, suggest which draft claim it belongs to (type match + issue date inside trip window), then attach it via two-phase handshake. Triggers: 补充发票, 补录发票, 补一张票, 漏了一张发票, 发票挂哪张单, supplemental invoice, add missing invoice."
description_zh: "发票补录：新收发票入库后，按类型匹配 + 开票日在行程区间内推荐归属的草稿单（±7 天门控），再走两段握手挂入。触发：补充发票、漏了一张票、这张票挂哪张单。"
description_en: "Supplemental invoice filing with claim-matching suggestions"
version: 1.0.0
display_name: "发票补录"
visibility: "public"
---

# 发票补录 Skill

## 触发场景

用户说「我又收到一张发票，帮我补进去」「上次那单漏了一张票」「这张票应该报在哪张单里？」

## 编排（写操作一律两段握手）

1. **入库**（若票还没进系统）
   - 有文件路径：`invoice_ingest(file_path)`（单段，属收取管线；重复票返回已拦截）
   - 已入库：直接下一步
2. **找候选单（只读，必调）**：`suggest_claim_for_invoice(invoice_id)`
   - 返回 `candidates`（≤3 条，含 `score` 与 `reasons`）
   - **把候选和理由念给用户**：「这张票最可能属于 FY-xxxx（类型匹配 + 开票日在行程区间内），要挂到这张吗？」
   - `candidates` 为空 → 说明「没有匹配的草稿单」，问用户是新建单还是挂到别处
3. **挂票（两段握手）**
   - 已有事项：`expense_add_invoices_proposal(claim_id, entry_id, [发票号])`
   - 单里还没有对应事项：先 `expense_add_entry_proposal(...)` 建事项（场景要素按类型填）
   - 两步都要走完 `confirm_execute(human_ack=true)`
4. **核对**：挂完调 `invoice_detail(invoice_id)` 确认状态，并向用户回报「已挂到 FY-xxxx」

## 业务规则

- 推荐**仅针对草稿单**（已提交的单不能改，需先撤回）
- 日期门控：发票开票日必须落在候选单事项日期的 ±7 天内，否则不推荐（防挂错单）
- 一票一报：票已在别的单里会被系统拦（`已被占用`），把原单号告诉用户
- 红字票补关联用 `red_invoice_list` 找未关联红票，再用 `red_invoice_link_proposal(red_invoice_id, original_invoice_id)`

## 缺什么问什么（每轮 1-2 问）

- 候选有多条且分数接近 → 问「是 XX 那次出差还是 YY？」
- 完全没候选 → 问「要新建一张报销单吗？事由是什么？」
- 单里没有对应事项 → 问「这笔是什么费用？（差旅/办公/招待…）」

## 异常处理（升级人工）

- 验真失败 → 告知用户票不可用及原因，不挂
- 票已被占用 → 给出原单号，让用户决定是否撤回原单
- 用户确认挂错单 → 提示需先撤下（`expense_*` 移除凭证）再挂新单
