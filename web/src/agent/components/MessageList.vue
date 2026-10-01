<!-- 消息列表：用户/助手气泡（用户消息纯文本，助手消息走 AssistantMarkdown）。
     有 blocks 的助手消息按时间线交错渲染：reasoning → ReasoningBlock、tool → 单 chip 行、text → markdown；
     旧数据（blocks 为空）仍走「思考 → 工具 chip → 正文」兼容布局 -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { AgentBlock, AgentMessage } from "../types";
import AssistantMarkdown from "./AssistantMarkdown.vue";
import ReasoningBlock from "./ReasoningBlock.vue";

const props = defineProps<{
  messages: AgentMessage[];
  streamingBlocks: AgentBlock[];
  streaming: boolean;
}>();

const scroller = ref<HTMLElement | null>(null);
async function scrollToBottom() {
  await nextTick();
  scroller.value?.scrollTo({ top: scroller.value.scrollHeight });
}
// deep：token 只增文本不改块数（相邻合并），浅比较会漏滚动
watch(
  () => [props.messages.length, props.streamingBlocks],
  scrollToBottom,
  { deep: true },
);

const STATUS_TEXT: Record<string, string> = { start: "运行中", done: "完成", failed: "失败" };

/** 流式块列表中「最后一块」才带光标/展开态 */
function isLastBlock(i: number): boolean {
  return i === props.streamingBlocks.length - 1;
}
/** 末块不是 text（如工具/思考）时，光标独立追加在列表尾 */
const trailingCursor = computed(() => props.streamingBlocks[props.streamingBlocks.length - 1]?.type !== "text");
</script>

<template>
  <div ref="scroller" class="msg-scroller">
    <div v-if="!messages.length && !streaming" class="msg-empty">
      你好，我是发票易助手。试试问：「本月有哪些待复核的发票？」
    </div>
    <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
      <div v-if="m.role === 'user'" class="msg-text">{{ m.content }}</div>
      <template v-else-if="m.blocks?.length">
        <!-- 时间线：按块序交错渲染 -->
        <template v-for="(b, i) in m.blocks" :key="i">
          <ReasoningBlock v-if="b.type === 'reasoning'" :text="b.text" />
          <div v-else-if="b.type === 'tool'" class="tool-chips">
            <span class="tool-chip" :class="b.status">
              {{ b.tool }} · {{ STATUS_TEXT[b.status] ?? b.status }}<template v-if="b.ms"> · {{ b.ms }}ms</template>
            </span>
          </div>
          <AssistantMarkdown v-else :text="b.text" />
        </template>
      </template>
      <template v-else>
        <!-- 旧数据兼容布局：思考 → 工具 chips → 正文 -->
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
      <template v-for="(b, i) in streamingBlocks" :key="i">
        <ReasoningBlock v-if="b.type === 'reasoning'" :text="b.text" :streaming="isLastBlock(i)" />
        <div v-else-if="b.type === 'tool'" class="tool-chips">
          <span class="tool-chip" :class="b.status">
            {{ b.tool }} · {{ STATUS_TEXT[b.status] ?? b.status }}<template v-if="b.ms"> · {{ b.ms }}ms</template>
          </span>
        </div>
        <AssistantMarkdown v-else :text="b.text" :streaming="isLastBlock(i)" />
      </template>
      <span v-if="trailingCursor" class="cursor">▍</span>
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
.cursor {
  animation: blink 1s step-start infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
</style>
