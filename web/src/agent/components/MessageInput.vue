<!-- 输入区：发送 / 取消 / 重试 -->
<script setup lang="ts">
import { ref } from "vue";
const props = defineProps<{ streaming: boolean; canRetry: boolean }>();
const emit = defineEmits<{ (e: "send", text: string): void; (e: "cancel"): void; (e: "retry"): void }>();
const text = ref("");

function onSend() {
  const t = text.value.trim();
  if (!t || props.streaming) return;
  emit("send", t);
  text.value = "";
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    onSend();
  }
}
</script>

<template>
  <div class="input-box">
    <textarea
      v-model="text"
      class="input-area"
      rows="2"
      placeholder="描述你的问题，Enter 发送 / Shift+Enter 换行"
      @keydown="onKeydown"
    />
    <div class="input-actions">
      <button v-if="canRetry && !streaming" class="btn ghost" @click="emit('retry')">重试</button>
      <button v-if="streaming" class="btn danger" @click="emit('cancel')">停止</button>
      <button v-else class="btn primary" :disabled="!text.trim()" @click="onSend">发送</button>
    </div>
  </div>
</template>

<style scoped>
.input-box {
  border-top: 1px solid #eef0f3;
  padding: 10px 12px;
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.input-area {
  flex: 1;
  resize: none;
  border: 1px solid #e2e5ea;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  outline: none;
}
.input-area:focus {
  border-color: #2563eb;
}
.input-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.btn {
  border: none;
  border-radius: 6px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn.primary { background: #2563eb; color: #fff; }
.btn.primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.danger { background: #fef2f2; color: #dc2626; }
.btn.ghost { background: #f2f4f7; color: #374151; }
</style>
