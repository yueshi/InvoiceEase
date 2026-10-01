<!-- 思考过程块（reasoning）：流式中默认展开显示「思考中…」，结束后自动折叠为一行、点击可再展开。
     正文一律纯文本插值 {{ }} 渲染——思考内容同样可能含注入载荷，严禁 v-html -->
<script setup lang="ts">
import { ref, watch } from "vue";

const props = defineProps<{
  text: string;
  streaming?: boolean;
}>();

// 流式中默认展开；归档消息（挂载时 streaming 非真）默认折叠
const expanded = ref(!!props.streaming);
watch(
  () => props.streaming,
  (now, prev) => {
    if (prev && !now) expanded.value = false; // 流结束 → 自动折叠
  },
);
</script>

<template>
  <div class="reasoning" :class="{ streaming }">
    <button class="reasoning-head" type="button" :disabled="streaming" @click="expanded = !expanded">
      {{ streaming ? "💭 思考中…" : `💭 已深度思考（点击${expanded ? "收起" : "展开"}）` }}
    </button>
    <div v-if="streaming || expanded" class="reasoning-body">{{ text }}</div>
  </div>
</template>

<style scoped>
.reasoning {
  max-width: 92%;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 6px;
}
.reasoning-head {
  display: block;
  border: none;
  background: none;
  padding: 0;
  font-family: inherit;
  font-size: 12px;
  color: #64748b;
  cursor: pointer;
}
.reasoning-head:disabled {
  cursor: default;
}
.reasoning-body {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
