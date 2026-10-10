---
name: travel-reimbursement
description: "Travel reimbursement end-to-end: rebuild trip itinerary from receipts, detect missing return leg / discontinuous routes / hotel-night mismatches, match per-diem & accommodation standards, then create the expense claim. Triggers: 出差报销, 差旅报销, 行程报销, 出差费用, 机票报销, 住宿报销, travel reimbursement."
description_zh: "差旅报销：从票据重建行程（去程/返程/接续/住宿晚数）、匹配住宿与伙食补助标准、缺失检测，再走两段握手建单。触发：出差报销、差旅报销、机票/住宿报销。"
description_en: "Travel reimbursement with itinerary reconstruction and standard matching"
version: 1.0.0
display_name: "差旅报销"
visibility: "public"
---

# 差旅报销 Skill

## 触发场景

用户说「上周去上海出差 3 天帮我报销」「这张机票和酒店发票报一下」——一次行程含交通 + 住宿 + 伙食补助。

## 编排（写操作一律两段握手）

1. **取票据**
   - `expense_eligible_invoices` 看可报池；`invoice_list` 按日期区间/关键词定位
   - 用户只给文件时：`invoice_ingest`（单段，属收取管线）
2. **建单（两段握手第一步）**
   - `expense_create_proposal(title, claim_type="travel")` → 把 preview 念给用户 → `confirm_execute(..., human_ack=true)`
3. **逐项建事项**（每个事项一次握手）
   - 交通：`expense_add_entry_proposal(..., scene_fields={"subtype":"transport", transport_mode, from_city, to_city, vehicle_no, travel_date})`
   - 住宿：`{"subtype":"accommodation", city, checkin, checkout, nights, rooms}`
   - 市内交通：`{"subtype":"local_transport", city, travel_date}`
   - 伙食补助：`{"subtype":"allowance", days, daily_standard, city}`（无需发票，系统按 天数×标准 生成内部凭证）
4. **挂票**：`expense_add_invoices_proposal(claim_id, entry_id, [发票号...])`，票必须在 `expense_eligible_invoices` 池里
5. **行程校验（只读，必调）**：`validate_trip_consistency(claim_id)`
   - `NO_RETURN_TRIP`：有去程无返程 → 主动问用户「返程票是否遗漏」
   - `ROUTE_DISCONTINUOUS`：段间城市不接续 → 问是否漏了中间段
   - `NIGHTS_MISMATCH` / `STAY_DATE_INVALID`：住宿晚数与出入住矛盾 → **必须让用户改正后再提交**
6. **标准校验（只读）**：`validate_meal_compliance(claim_id)` —— 伙食补助超标提示
7. **提交**：`expense_submit_proposal(claim_id)` → 用户确认 → `confirm_execute`（内部自动跑 6 项校验，FAIL 会被拦）

## 业务规则

- 补助日标准默认取公司配置（`/expenses/config` 的 `travel_allowance_daily_standard`），用户显式给了 `daily_standard` 则以用户为准
- 住宿/交通标准来自政策表（`expense_policies`：travel/accommodation、travel/local_transport_day）；`validate_meal_compliance` 会把超标分级：容忍值内 = 提示，超容忍 = 阻断
- **只报原件**：图片/截图不收（合规硬约束），提示用户回原邮箱取 PDF/OFD/XML

## 缺什么问什么（每轮 1-2 问）

- 缺行程日期 → 问「几号到几号？」
- 有交通无住宿但天数为多日 → 问「住宿发票有吗？还是当天往返？」
- 住宿城市与交通到达城市不一致 → 问「住宿是在 XX 还是 YY？」

## 异常处理（升级人工）

- 票据验真失败/重复拦截 → 告知财务待办与原因，**不继续**
- `validate_trip_consistency` 只出 warning → 可在回复里附「需人工确认：<原因>」后提交
- `expense_submit_proposal` 确认时抛 `validate_expense FAIL` → 把 errors 原文转述用户，不要重试同一提案（token 已失效）
