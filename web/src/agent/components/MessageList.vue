<!-- 消息列表：用户/助手气泡 + 工具调用 chip（用户消息纯文本，助手消息走 AssistantMarkdown）。
     助手侧展示顺序：思考过程 → 工具调用 → 最终回答正文 -->
<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import type { AgentMessage, AgentToolCall } from "../types";
import AssistantMarkdown from "./AssistantMarkdown.vue";
import ReasoningBlock from "./ReasoningBlock.vue";

const props = withDefaults(
  defineProps<{
    messages: AgentMessage[];
    streamingText: string;
    streamingTools: AgentToolCall[];
    streaming: boolean;
    streamingReasoning?: string;
  }>(),
  { streamingReasoning: "" },
);

const scroller = ref<HTMLElement | null>(null);
async function scrollToBottom() {
  await nextTick();
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
}
watch(
  () => [props.messages.length, props.streamingText, props.streamingReasoning.length, props.streamingTools.length],
  scrollToBottom,
);

const STATUS_TEXT: Record<string, string> = { start: "运行中", done: "完成", failed: "失败" };
</script>

<template>
  <div ref="scroller" class="msg-scroller">
    <div v-if="!messages.length && !streaming" class="msg-empty">
      你好，我是发票易助手。试试问：「本月有哪些待复核的发票？」
    </div>
    <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
      <div v-if="m.role === 'user'" class="msg-text">{{ m.content }}</div>
      <template v-else>
        <ReasoningBlock v-if="m.reasoning" :text="m.reasoning" />
        <div v-if="m.tool_calls?.length" class="tool-chips">
          <span v-for="(t, i) in m.tool_calls" :key="i" class="tool-chip" :class="t.status">
            {{ t.tool }} · {{ STATUS_TEXT[t.status] ?? t.status }}<template v-if="t.ms"> · {{ t.ms }}ms</template>
          </span>
        </div>
        <AssistantMarkdown :text="m.content" />
      </template>
    </div>
    <div v-if="streaming" class="msg assistant">
      <ReasoningBlock v-if="streamingReasoning" :text="streamingReasoning" streaming />
      <div v-if="streamingTools.length" class="tool-chips">
        <span v-for="(t, i) in streamingTools" :key="i" class="tool-chip" :class="t.status">
          {{ t.tool }} · {{ STATUS_TEXT[t.status] ?? t.status }}<template v-if="t.ms"> · {{ t.ms }}ms</template>
        </span>
      </div>
      <AssistantMarkdown :text="streamingText" streaming />
    </div>
  </div>
</template>

<style scoped>
.msg-scroller {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}
.msg-empty {
  color: #8a94a6;
  font-size: 13px;
  margin-top: 24px;
  text-align: center;
}
.msg {
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
}
.msg.user {
  align-items: flex-end;
}
.msg-text {
  max-width: 92%;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.user .msg-text {
  background: #2563eb;
  color: #fff;
}
.tool-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.tool-chip {
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  background: #eef2ff;
  color: #4f46e5;
}
.tool-chip.done { background: #ecfdf5; color: #059669; }
.tool-chip.failed { background: #fef2f2; color: #dc2626; }
</style>
