<!--
  报销单草稿预览卡（P2 §6.2「报销单预览确认」）。
  只展示 + 注入预置消息：点「确认提交」不直接写库，而是替用户说一句话，
  由 Agent 按两段握手流程继续（点卡 ≠ 用户已确认，见 system_prompts 出卡规则）。
-->
<script setup lang="ts">
import { computed } from "vue";
import type { DraftCard } from "../claimCardSpec";

const props = withDefaults(defineProps<{ card: DraftCard; disabled?: boolean }>(), { disabled: false });
const emit = defineEmits<{ (e: "submit", prompt: string): void }>();

const statusLabel: Record<string, string> = {
  draft: "草稿",
  pending_approval: "待审批",
  approved: "已通过",
  rejected: "已驳回",
  withdrawn: "已撤回",
};

const isDraft = computed(() => !props.card.status || props.card.status === "draft");
const statusText = computed(() =>
  props.card.status ? statusLabel[props.card.status] ?? props.card.status : "草稿");

function onSubmit() {
  const no = props.card.claim_no ? `${props.card.claim_no} ` : "";
  emit("submit", `请帮我提交报销单 ${no}`.trim());
}
</script>

<template>
  <div class="cdc-card">
    <div class="cdc-head">
      <span class="cdc-label">报销单预览</span>
      <span class="cdc-status">{{ statusText }}</span>
    </div>
    <div v-if="card.claim_no" class="cdc-row">
      <span class="cdc-key">单号</span><span class="cdc-val">{{ card.claim_no }}</span>
    </div>
    <div v-if="card.title" class="cdc-row">
      <span class="cdc-key">事由</span><span class="cdc-val">{{ card.title }}</span>
    </div>
    <div class="cdc-entries">
      <div v-for="(e, i) in card.entries" :key="i" class="cdc-entry">
        <span class="cdc-entry-title">{{ e.title ?? "—" }}</span>
        <span class="cdc-entry-amount">{{ e.amount ?? "" }}</span>
      </div>
      <div v-if="!card.entries.length" class="cdc-empty">暂无明细</div>
    </div>
    <div v-if="card.total_amount" class="cdc-total">
      <span>合计</span><span class="cdc-total-amount">{{ card.total_amount }} 元</span>
    </div>
    <div class="cdc-actions">
      <button
        v-if="isDraft" class="cdc-submit" type="button" :disabled="disabled"
        @click="!disabled && onSubmit()"
      >
        确认提交
      </button>
    </div>
  </div>
</template>

<style scoped>
.cdc-card {
  border: 1px solid #e6e8eb;
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
  background: #fff;
}
.cdc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.cdc-label { font-weight: 600; font-size: 13px; }
.cdc-status {
  font-size: 12px;
  color: var(--c-sub, #8c8c8c);
  border: 1px solid #eee;
  border-radius: 10px;
  padding: 0 8px;
}
.cdc-row { display: flex; gap: 8px; font-size: 13px; line-height: 1.9; }
.cdc-key { color: var(--c-sub, #8c8c8c); min-width: 40px; }
.cdc-val { flex: 1; word-break: break-all; }
.cdc-entries {
  border-top: 1px dashed #f0f0f0;
  margin-top: 6px;
  padding-top: 6px;
}
.cdc-entry {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  line-height: 1.9;
}
.cdc-entry-title { color: #333; }
.cdc-entry-amount { color: #333; }
.cdc-empty { color: var(--c-sub, #8c8c8c); font-size: 13px; }
.cdc-total {
  display: flex;
  justify-content: space-between;
  border-top: 1px solid #f0f0f0;
  margin-top: 6px;
  padding-top: 6px;
  font-size: 14px;
  font-weight: 600;
}
.cdc-actions { display: flex; justify-content: flex-end; margin-top: 8px; }
.cdc-submit[disabled] { opacity: 0.5; cursor: not-allowed; }
.cdc-submit {
  padding: 4px 14px;
  border-radius: 4px;
  border: 1px solid #1677ff;
  background: #1677ff;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}
</style>
