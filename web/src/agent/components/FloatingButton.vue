<!-- 右下角悬浮球：点开 Agent 抽屉 -->
<script setup lang="ts">
import { useAgentStore } from "../store";
const store = useAgentStore();
</script>

<template>
  <button class="agent-fab" title="智能助手" @click="store.openDrawer()">AI</button>
</template>

<style scoped>
.agent-fab {
  position: fixed;
  right: 28px;
  bottom: 32px;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  cursor: pointer;
  box-shadow: 0 6px 16px rgba(37, 99, 235, 0.35);
  z-index: 900;
}
.agent-fab:hover {
  filter: brightness(1.06);
}
</style>
