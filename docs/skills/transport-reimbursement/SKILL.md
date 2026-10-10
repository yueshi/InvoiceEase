---
name: transport-reimbursement
description: "Transport reimbursement: validate origin/destination continuity across legs, match city-transport daily caps, flag discontinuous routes, then create the claim. Triggers: 打车报销, 高铁报销, 机票报销, 交通费报销, 市内交通, transport reimbursement, taxi reimbursement."
description_zh: "交通报销：起终点接续校验、市内交通日限额匹配、路线不连续预警，再走两段握手建单。触发：打车报销、高铁/机票报销、交通费报销。"
description_en: "Transport reimbursement with route continuity validation"
version: 1.0.0
display_name: "交通报销"
visibility: "public"
---

# 交通报销 Skill

## 触发场景

用户说「这周打车的发票报销」「高铁票报一下」「机场往返打车费」。

## 编排（写操作一律两段握手）

1. **取票据**：`expense_eligible_invoices`（打车/铁路/航空票）+ `invoice_list` 关键词定位
2. **建单**：`expense_create_proposal(title, claim_type="travel")` → 展示 preview → `confirm_execute(human_ack=true)`
3. **建事项**（每个交通段一条，便于接续校验）
   - 城际：`expense_add_entry_proposal(..., entry_type="travel", scene_fields={"subtype":"transport", transport_mode, from_city, to_city, vehicle_no, travel_date})`
   - 市内：`scene_fields={"subtype":"local_transport", city, travel_date}`
4. **挂票**：`expense_add_invoices_proposal(claim_id, entry_id, [发票号])`
5. **接续校验（只读，必调）**：`validate_trip_consistency(claim_id)`
   - `ROUTE_DISCONTINUOUS`：上一段到 A、下一段自 B 出发 → 问用户是否漏了中间段
   - `NO_RETURN_TRIP`：单程票 → 问是否当天往返/返程票遗漏
6. **标准校验（只读）**：`validate_meal_compliance(claim_id)` —— 市内交通按 `travel/local_transport_day` 日限额（单日多笔合并判定）
7. **提交**：`expense_submit_proposal(claim_id)` → 用户确认 → `confirm_execute`

## 业务规则

- 市内交通日限额来自政策表（`expense_policies`：travel/local_transport_day）；同一天多笔市内交通**按日合计**与该标准比对
- `vehicle_no`（车次/航班号）尽量让用户补：机酒行程核对与税务检查都看它
- **里程合理性暂不自动判定**（需地图距离数据，未接入）——用户主动质疑里程时，如实说明「暂无法自动校验，建议人工核对行程单」

## 缺什么问什么（每轮 1-2 问）

- 城际交通缺 `travel_date` → 问「几号的车/机？」
- 只有去程 → 问「返程也报吗？」
- 市内交通单日多笔 → 问「同一天这几笔是同一行程吗？」

## 异常处理（升级人工）

- 同一车次/航班号重复出现 → 提示可能重复报销（系统查重会拦，先告知用户）
- 交通段跨越未申报城市 → 提示「行程与住宿/补助的城市不一致，请确认」
