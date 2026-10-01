<!-- Agent 面板：挤压式右侧栏（inline flex 列，不遮盖主内容）+ 左缘拖拽调宽
     会话切换 + 消息流 + 输入 + 页面上下文 chip -->
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useAgentStore } from "../store";
import MessageList from "./MessageList.vue";
import MessageInput from "./MessageInput.vue";
import type { ChatContext } from "../types";

const store = useAgentStore();
const route = useRoute();

// 每次打开面板刷新会话列表：ensureSession 只在无会话时创建，「首轮自动标题」需下次打开才可见
watch(
  () => store.drawerOpen,
  (open) => {
    if (open) void store.loadSessions();
  },
);

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

// ==== 拖拽调宽 ====
const dragging = ref(false);
let startX = 0;
let startWidth = 0;

function onDragStart(e: MouseEvent) {
  dragging.value = true;
  startX = e.clientX;
  startWidth = store.drawerWidth;
  // 拖拽期锁定全局 cursor + 禁用选择：鼠标移出手柄命中区后不变回默认光标
  const prevCursor = document.body.style.cursor;
  const prevSelect = document.body.style.userSelect;
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";
  // 面板在右，往左拖 = 加宽，故 dx 取反
  const onMove = (ev: MouseEvent) => store.setDrawerWidth(startWidth + (startX - ev.clientX));
  const onUp = () => {
    dragging.value = false;
    document.body.style.cursor = prevCursor;
    document.body.style.userSelect = prevSelect;
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

/** 双击手柄复位默认宽度 */
function resetWidth() {
  store.setDrawerWidth(440);
}
</script>

<template>
  <aside v-if="store.drawerOpen" class="agent-panel" :style="{ width: store.drawerWidth + 'px' }">
    <div
      class="resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="拖动调整助手面板宽度"
      @mousedown.prevent="onDragStart"
      @dblclick="resetWidth"
    >
      <div class="handle-pill" :class="{ dragging }"><i /><i /><i /></div>
    </div>
    <header class="panel-header">
      <span class="panel-title">智能助手</span>
      <button class="panel-close" aria-label="关闭" @click="store.closeDrawer()">✕</button>
    </header>
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
      :streaming-reasoning="store.streamingReasoning"
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
  </aside>
</template>

<style scoped>
.agent-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100vh;
  flex-shrink: 0;
  background: #fff;
  border-left: 1px solid #eef0f3;
  box-shadow: -2px 0 8px rgba(15, 23, 42, 0.04);
}
/* 左缘拖拽命中区：横跨分界线（主内容侧 5px + 面板内 17px）。
   实测教训：原先是 8px 纯内侧条——用户瞄准的「分界线」本身恰是死区（elementFromPoint
   命中 aside 边框而非手柄），抓不到。加宽横跨 + hover 淡蓝提示条解决可发现性。 */
.resize-handle {
  position: absolute;
  left: -6px;
  top: 0;
  bottom: 0;
  width: 22px;
  cursor: col-resize;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.12s;
}
.resize-handle:hover,
.resize-handle:active {
  background: rgba(37, 99, 235, 0.08);
}
.handle-pill {
  display: flex;
  align-items: center;
}
.handle-pill i {
  display: block;
  width: 3px;
  height: 18px;
  background: #cbd5e1;
  border-radius: 2px;
  margin: 0 1px;
}
.resize-handle:hover .handle-pill i {
  background: #2563eb;
}
.handle-pill.dragging i {
  background: #1d4ed8;
}
.panel-header {
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  border-bottom: 1px solid #eef0f3;
  flex-shrink: 0;
}
.panel-title {
  font-size: 14px;
  font-weight: 600;
}
.panel-close {
  border: none;
  background: transparent;
  color: #8a94a6;
  font-size: 13px;
  line-height: 1;
  padding: 4px 6px;
  border-radius: 6px;
  cursor: pointer;
}
.panel-close:hover {
  background: #f2f4f7;
  color: #374151;
}
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
