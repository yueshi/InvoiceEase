<!--
  对话式报销（P2 §6.2）。与右侧 Agent 抽屉**共用同一 Pinia 会话**
  （切页不丢上下文），区别只是全屏呈现 + 把 5 个领域入口卡片摆在显眼处。
  写操作仍走两段握手：本页不做任何直接落库。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useAgentStore } from "../agent/store";
import MessageList from "../agent/components/MessageList.vue";
import MessageInput from "../agent/components/MessageInput.vue";
import EntryCards from "../agent/components/EntryCards.vue";
import type { ChatContext } from "../agent/types";

const store = useAgentStore();
const route = useRoute();
const inputRef = ref<InstanceType<typeof MessageInput> | null>(null);

const showEntries = computed(() => !store.messages.length && !store.streaming);

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

/** 入口卡与卡片按钮：只预填，不替用户发送。
 *  卡片按钮的文案由模型给出（用户只看到 label 字段）——预填后用户能先核对。 */
function onEntryPick(prompt: string) {
  inputRef.value?.fill(prompt);
}

/** 重试：重发最后一条用户消息（与抽屉同逻辑；直接绑 onSend 会发出 undefined） */
function onRetry() {
  const lastUser = [...store.messages].reverse().find((m) => m.role === "user");
  if (lastUser) void onSend(lastUser.content);
}

onMounted(async () => {
  await store.loadSessions();
  await store.ensureSession();
});
</script>

<template>
  <div class="chat-expense">
    <header class="ce-head">
      <h2 class="ce-title">报销助手</h2>
      <p class="ce-desc">
        一句话就能发起报销；助手会先查票、再逐项确认，最后生成报销单草稿等你点头。
      </p>
    </header>
    <div class="ce-body">
      <div v-if="showEntries" class="ce-entries">
        <EntryCards @pick="onEntryPick" />
      </div>
      <MessageList
        :messages="store.messages"
        :streaming-blocks="store.streamingBlocks"
        :streaming="store.streaming"
        @chat-action="onEntryPick"
      />
      <div v-if="store.error" class="ce-err">
        {{ store.error.message }}（{{ store.error.code }}）
      </div>
      <MessageInput
        ref="inputRef"
        :streaming="store.streaming"
        :can-retry="!store.streaming && !!store.error"
        @send="onSend"
        @cancel="store.cancel()"
        @retry="onRetry"
      />
    </div>
  </div>
</template>

<style scoped>
.chat-expense {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  max-width: 880px;
  margin: 0 auto;
}
.ce-head { padding: 4px 0 10px; }
.ce-title { font-size: 18px; font-weight: 600; margin: 0 0 4px; }
.ce-desc { font-size: 13px; color: var(--c-sub, #8c8c8c); margin: 0; }
.ce-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ce-entries { padding: 4px 0; }
.ce-err {
  font-size: 13px;
  color: #cf1322;
  background: #fff2f0;
  border: 1px solid #ffccc7;
  border-radius: 6px;
  padding: 6px 10px;
}
</style>
