<!-- Agent 抽屉：会话切换 + 消息流 + 输入 + 页面上下文 chip -->
<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useAgentStore } from "../store";
import MessageList from "./MessageList.vue";
import MessageInput from "./MessageInput.vue";
import type { ChatContext } from "../types";

const store = useAgentStore();
const route = useRoute();

const contextChip = computed(() => {
  const bits: string[] = [route.path];
  const q = route.query;
  if (q.invoice_id) bits.push(`发票#${q.invoice_id}`);
  if (q.claim_id) bits.push(`报销单#${q.claim_id}`);
  return bits.join(" · ");
});

function buildContext(): ChatContext {
  const q = route.query;
  return {
    page: route.path,
    invoice_id: q.invoice_id ? Number(q.invoice_id) : null,
    claim_id: q.claim_id ? Number(q.claim_id) : null,
    receipt_id: q.receipt_id ? Number(q.receipt_id) : null,
    month: typeof q.month === "string" ? q.month : null,
  };
}

async function onSend(text: string) {
  await store.sendMessage(text, buildContext());
}

function onRetry() {
  const lastUser = [...store.messages].reverse().find((m) => m.role === "user");
  if (lastUser) onSend(lastUser.content);
}
</script>

<template>
  <a-drawer
    :open="store.drawerOpen"
    placement="right"
    :width="440"
    :closable="true"
    :body-style="{ padding: 0, display: 'flex', flexDirection: 'column', height: '100%' }"
    title="智能助手"
    @close="store.closeDrawer()"
  >
    <div class="drawer-head">
      <select
        class="session-select"
        :value="store.currentSessionId ?? undefined"
        :disabled="store.streaming"
        @change="store.switchSession(Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="s in store.sessions" :key="s.id" :value="s.id">
          {{ s.title || "新会话" }}
        </option>
      </select>
      <button class="head-btn" @click="store.createSession()">新建</button>
      <button
        class="head-btn danger"
        :disabled="!store.currentSessionId || store.streaming"
        @click="store.currentSessionId && store.removeSession(store.currentSessionId)"
      >删除</button>
    </div>
    <div class="ctx-chip">上下文：{{ contextChip }}</div>
    <MessageList
      :messages="store.messages"
      :streaming-text="store.streamingText"
      :streaming-tools="store.streamingTools"
      :streaming="store.streaming"
    />
    <div v-if="store.error" class="err-banner">
      {{ store.error.message }}（{{ store.error.code }}）
    </div>
    <MessageInput
      :streaming="store.streaming"
      :can-retry="!store.streaming && !!store.error"
      @send="onSend"
      @cancel="store.cancel()"
      @retry="onRetry"
    />
  </a-drawer>
</template>

<style scoped>
.drawer-head {
  display: flex;
  gap: 8px;
  padding: 10px 12px 6px;
}
.session-select {
  flex: 1;
  border: 1px solid #e2e5ea;
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 13px;
  background: #fff;
}
.head-btn {
  border: 1px solid #e2e5ea;
  background: #fff;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.head-btn.danger { color: #dc2626; }
.head-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ctx-chip {
  padding: 0 12px 8px;
  font-size: 12px;
  color: #8a94a6;
}
.err-banner {
  margin: 0 12px 8px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 12px;
}
</style>
