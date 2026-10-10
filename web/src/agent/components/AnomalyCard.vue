<!--
  异常卡（P2 §6.2「异常提示与处理」）。
  - 默认展开：异常必须被看见，不做折叠（渐进式披露用于明细，不用于风险提示）
  - 选项点选 = 预填一句话到输入框（用户按发送才发出），不直接执行任何写操作
  - **按钮文字就是将要发出的那句话**（prompt），label 只作 tooltip：
    模型同时给 label/prompt 两个字段，若按钮显示 label 而实际发送 prompt，
    用户点「补充材料」可能发出「我确认，请直接提交」——§7.5 认"用户显式确认"，
    那等于伪造确认记录。所见即所发。
  - 文案不承诺「自动通过/帮你放行」：特批是人工决策（spec §4.3 责任承担节点）
-->
<script setup lang="ts">
import type { AnomalyCard } from "../claimCardSpec";

withDefaults(defineProps<{ card: AnomalyCard; disabled?: boolean }>(), { disabled: false });
const emit = defineEmits<{ (e: "choose", prompt: string): void }>();
</script>

<template>
  <div class="ac-card">
    <div class="ac-head">
      <span class="ac-icon" aria-hidden="true">⚠</span>
      <span class="ac-title">需处理</span>
      <span v-if="card.kind" class="ac-kind">{{ card.kind }}</span>
    </div>
    <div class="ac-message">{{ card.message }}</div>
    <div v-if="(card.options ?? []).length" class="ac-options">
      <button
        v-for="(o, i) in card.options ?? []"
        :key="i"
        class="ac-option"
        type="button"
        :title="o.label !== o.prompt ? o.label : undefined"
        :disabled="disabled"
        @click="!disabled && emit('choose', o.prompt)"
      >
        {{ o.prompt }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.ac-card {
  border: 1px solid #ffccc7;
  background: #fff2f0;
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
}
.ac-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.ac-icon { color: #cf1322; }
.ac-title { font-weight: 600; font-size: 13px; color: #cf1322; }
.ac-kind {
  font-size: 11px;
  color: #a8071a;
  border: 1px solid #ffa39e;
  border-radius: 10px;
  padding: 0 6px;
}
.ac-message { font-size: 13px; line-height: 1.7; color: #333; }
.ac-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.ac-option {
  padding: 3px 12px;
  border-radius: 4px;
  border: 1px solid #d9d9d9;
  background: #fff;
  font-size: 13px;
  cursor: pointer;
}
.ac-option:hover { border-color: #1677ff; color: #1677ff; }
.ac-option[disabled] { opacity: 0.5; cursor: not-allowed; }
</style>
