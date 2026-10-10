---
name: meal-reimbursement
description: "Meal & entertainment reimbursement: match per-head caps and per-diem meal allowances against company policy, check headcount, distinguish 招待 (business entertainment) from 福利费 (employee welfare), then create the claim. Triggers: 吃饭报销, 餐饮报销, 招待费, 餐补, 聚餐报销, meal reimbursement, entertainment expense."
description_zh: "餐饮/招待报销：人均限额与餐补标准比对（含容忍值）、招待人数校验、招待与福利费归类判断，再走两段握手建单。触发：吃饭报销、招待费、餐补、聚餐报销。"
description_en: "Meal & entertainment reimbursement with per-head cap checks"
version: 1.0.0
display_name: "餐饮报销"
visibility: "public"
---

# 餐饮报销 Skill

## 触发场景

用户说「请客户吃饭的发票报一下」「团建聚餐报销」「出差餐补算一下」。

## 编排（写操作一律两段握手）

1. **取票据**：`invoice_list`（关键词 = 餐饮公司名）或 `expense_eligible_invoices`
2. **建单**：`expense_create_proposal(title, claim_type="entertainment")`（团建/员工聚餐用 `welfare`）→ 展示 preview → `confirm_execute(human_ack=true)`
3. **建事项**：`expense_add_entry_proposal(..., entry_type="entertainment", scene_fields={"guests": 招待对象, "headcount": 人数})`
   - **人数必填**：没有人数就算不出人均，标准校验也无从谈起
4. **挂票**：`expense_add_invoices_proposal(claim_id, entry_id, [发票号])`
5. **标准校验（只读，必调）**：`validate_meal_compliance(claim_id)`
   - `PER_HEAD_OVER_STANDARD`：人均超标。容忍值内 → 提示用户；超容忍 → 让用户补说明或改走特批
   - `HEADCOUNT_UNKNOWN`：人数缺失 → 追问人数
   - `NO_POLICY_CONFIGURED`：公司还没配人均标准 → 说明「暂无法判定是否超标」，不阻断
6. **提交**：`expense_submit_proposal(claim_id)` → 用户确认 → `confirm_execute`

## 出卡（前端会渲染成卡片）

- 报销单草稿成型时出一张草稿卡（`expense-draft`），让用户核对单号/金额/明细再提交
- 校验工具返回 warning/error 时出一张异常卡（`anomaly`），`message` 引用工具原文，
  选项给用户视角的动作（补充材料 / 申请特批 / 修改金额）
- **点卡片按钮不等于用户已确认**：那只是替用户说了一句话，写操作仍走两段握手

## 业务规则

- 人均标准来自政策表（`expense_policies`：entertainment/per_head），容忍值同表 tolerance
- **招待 vs 福利费**：受益对象是**外部客户** → 招待费（`entertainment`）；受益对象是**本企业员工**（团建/聚餐/节日福利/体检/办公饮用水）→ 福利费（`welfare`，有 14% 限额口径）。拿不准就问用户「参加的是客户还是同事？」
- 招待对象与人数是税务检查要点，**不要替用户编**

## 缺什么问什么（每轮 1-2 问）

- 招待 → 问「招待的是哪家客户？一共几个人？」
- 团建/聚餐 → 问「是员工内部聚餐吗？」（确认归类福利）

## 异常处理（升级人工）

- 人均超容忍值 → 生成超标说明提示：「需在报销单说明超标原因，审批人会看到」
- 大量小额餐饮同一天多张 → 提示「同一场次请合为一笔，避免拆分规避限额」
