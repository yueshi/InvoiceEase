---
name: over-budget
description: "Over-standard / over-budget handling: when a validation returns warning or error (per-head cap, per-diem, large amount, budget balance), explain the rule breached, draft the justification template, and route to the extra approver. Triggers: 超标, 超标准, 超预算, 超过限额, 超标说明, 特批申请, over budget, over standard."
description_zh: "超标处理：校验返回 warning/error 时，说明违反的规则、生成超标说明模板、提示加签与特批路径。触发：超标、超预算、超限额、超标说明、特批。"
description_en: "Over-standard & over-budget handling with justification templates"
version: 1.0.0
display_name: "超标处理"
visibility: "public"
---

# 超标处理 Skill

## 触发场景

校验工具返回了问题，或用户直接问「这个超标了怎么办？」「能特批吗？」

触发来源（只读，先调再说话）：
- `validate_meal_compliance(claim_id)` → `OVER_STANDARD` / `PER_HEAD_OVER_STANDARD`（餐补/人均超标准）
- `validate_expense(claim_id)` → `AMOUNT_TOO_LARGE`（大额）/ `OVER_BUDGET`（超预算）/ `AMOUNT_AT_THRESHOLD`（卡在阈值）
- `validate_trip_consistency(claim_id)` → 行程类问题（应转交差旅 Skill 补正，而非特批）

## 处理流程

1. **说清违反了什么**（引用工具原文，不转述不加工）
   - 「招待人均 450 元，超公司标准 300 元，超出 150 元（容忍值 50 元）」
   - 「单笔 8000 元，达到大额阈值 5000 元，需人工审批」
2. **分级处置**

   | 工具返回 | 含义 | 处置 |
   |---|---|---|
   | `outcome=NEEDS_REVIEW`（仅 warning） | 需人工看一眼，不阻断 | 提示用户「提交后会标记需人工确认」；确认后正常提交 |
   | `outcome=FAIL`（含 error） | 逻辑矛盾或超容忍值 | **不得提交**；按下表给方案 |
   | `NO_POLICY_CONFIGURED` | 公司没配标准 | 说明「暂无法判定」，不当违规处理 |

3. **FAIL 的三条出路**（让用户选，别替他决定）
   - **改单**：金额/标准填错 → 回对应 Skill 修正事项（`expense_*` 移除后重挂）
   - **补说明**：业务确实超标（如招待重要客户）→ 生成说明模板（下）
   - **特批**：走加签审批（**人工环节，Agent 不代劳**）
4. **超标说明模板**（填好后由用户确认，随单提交）

   ```
   【超标说明】
   事项：<事项标题>
   标准：<标准值> 元/<单位>（来源：公司费用标准）
   实际：<实际值> 元
   超出：<超出金额> 元
   原因：<用户给出的业务理由>
   审批建议：<需加签的审批人/层级>
   ```
5. **提交**（两段握手）：`expense_submit_proposal(claim_id)` → 展示 preview + 超标说明 → `confirm_execute(human_ack=true)`
   - 注意：`validate_expense` 的 **error 会在确认时抛错阻断**（这是设计，不是 bug）；先解决再提交

## 出卡（前端会渲染成卡片）

- 报销单草稿成型时出一张草稿卡（`expense-draft`），让用户核对单号/金额/明细再提交
- 校验工具返回 warning/error 时出一张异常卡（`anomaly`），`message` 引用工具原文，
  选项给用户视角的动作（补充材料 / 申请特批 / 修改金额）
- **点卡片按钮不等于用户已确认**：那只是替用户说了一句话，写操作仍走两段握手

## 硬约束（不要越界）

- **不替用户编造理由**：超标原因必须用户给出；用户说不出 → 建议先与主管沟通
- **不绕过校验**：任何"帮你算低一点/拆成两笔"的请求都拒绝，并说明这会构成拆分规避
- **特批是人工决策**（spec §4.3 责任承担节点）：Agent 只负责把材料和路径准备好
- 对自动审批/放行类请求：明确说明需财务在后台确认，Agent 无此权限

## 缺什么问什么（每轮 1-2 问）

- 超标原因 → 问「这次超标的业务原因是什么？（如客户重要程度、住宿紧张）」
- 是否已知会主管 → 问「是否已与主管沟通？」

## 异常处理（升级人工）

- 用户坚持"就这样报" → 说明「确认后仍会被审批人看到超标项，且系统会阻断含 error 的提交」，请其与财务沟通
- 政策本身不合理（如标准过低）→ 引导用户走「政策规则变更」流程（财务负责人审批，spec §4.3 规则变更节点）
