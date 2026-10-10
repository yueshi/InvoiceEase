<!--
  高风险操作二次确认（v1.1 §7.5.1）。
  Web 人工通道：前端 confirm modal 即人工确认（不走 proposal_token 协议）；
  high 档（放行拦截 / 删除账号 / 大额提交）强制填理由 + 文案防误点。
-->
<script setup lang="ts">
import { computed, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    preview: Record<string, unknown>;
    riskLevel?: "low" | "medium" | "high";
    requireReason?: boolean;
    confirmText?: string;
  }>(),
  { riskLevel: "medium", requireReason: false, confirmText: "" },
);

const emit = defineEmits<{
  (e: "confirm", reason: string): void;
  (e: "cancel"): void;
  (e: "update:open", v: boolean): void;
}>();

const reason = ref("");
watch(
  () => props.open,
  () => {
    reason.value = "";
  },
);

const isHigh = computed(() => props.riskLevel === "high");
const canConfirm = computed(() => !props.requireReason || reason.value.trim().length > 0);
const confirmLabel = computed(
  () => props.confirmText || (isHigh.value ? "我已确认执行" : "确认"),
);
const riskHint = computed(() => {
  if (props.riskLevel === "high") return "高风险操作：请核对预览内容后确认，操作不可撤销";
  if (props.riskLevel === "medium") return "请核对预览内容后确认";
  return "";
});
const entries = computed(() => Object.entries(props.preview || {}));

function formatVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function close() {
  emit("cancel");
  emit("update:open", false);
}

function onConfirm() {
  if (!canConfirm.value) return;
  emit("confirm", reason.value.trim());
}
</script>

<template>
  <div v-if="open" class="cm-mask" @click.self="close()">
    <div class="cm-panel" role="dialog" :aria-label="title">
      <div class="cm-title">{{ title }}</div>
      <div v-if="riskHint" class="cm-risk" :class="`cm-risk-${riskLevel}`">{{ riskHint }}</div>
      <div class="cm-preview">
        <div v-for="[k, v] in entries" :key="k" class="cm-row">
          <span class="cm-key">{{ k }}</span>
          <span class="cm-val">{{ formatVal(v) }}</span>
        </div>
        <div v-if="!entries.length" class="cm-empty">（无预览内容）</div>
      </div>
      <textarea
        v-if="requireReason"
        v-model="reason"
        class="cm-reason"
        rows="2"
        placeholder="请填写操作理由（必填，将记入审计）"
      />
      <div class="cm-actions">
        <button class="cm-cancel" type="button" @click="close()">取消</button>
        <button
          class="cm-confirm"
          type="button"
          :class="{ danger: isHigh }"
          :disabled="!canConfirm"
          @click="onConfirm()"
        >
          {{ confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cm-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
}
.cm-panel {
  width: 460px;
  max-width: 92vw;
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
}
.cm-title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 8px;
}
.cm-risk {
  font-size: 13px;
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 10px;
}
.cm-risk-high {
  background: #fff2f0;
  color: #cf1322;
  border: 1px solid #ffccc7;
}
.cm-risk-medium {
  background: #fffbe6;
  color: #ad6800;
  border: 1px solid #ffe58f;
}
.cm-preview {
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
  padding: 10px;
  margin-bottom: 12px;
  max-height: 40vh;
  overflow: auto;
}
.cm-row {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.8;
}
.cm-key {
  color: var(--c-sub, #8c8c8c);
  min-width: 96px;
}
.cm-val {
  flex: 1;
  word-break: break-all;
}
.cm-empty {
  color: var(--c-sub, #8c8c8c);
  font-size: 13px;
}
.cm-reason {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 12px;
  padding: 6px 8px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  font-size: 13px;
}
.cm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.cm-actions button {
  padding: 5px 16px;
  border-radius: 4px;
  border: 1px solid #d9d9d9;
  background: #fff;
  cursor: pointer;
}
.cm-actions button.cm-confirm {
  background: #1677ff;
  border-color: #1677ff;
  color: #fff;
}
.cm-actions button.cm-confirm.danger {
  background: #cf1322;
  border-color: #cf1322;
}
.cm-actions button[disabled] {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
