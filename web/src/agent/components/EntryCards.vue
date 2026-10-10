<!--
  领域入口卡（P2 §6.2「报销发起对话」的 5 个入口）。
  点入口 = 把预置提示词填进输入框（**不直接发送**）：用户通常还要补细节
  （「上周去上海出差 3 天」），填进去让他改比替他发出去更省事。
  提示词用词对齐 docs/skills/*/SKILL.md 的 description 触发词，防两边漂移。
-->
<script setup lang="ts">
interface Entry {
  key: string;
  label: string;
  hint: string;
  prompt: string;
}

const ENTRIES: Entry[] = [
  { key: "travel", label: "出差报销", hint: "机票/住宿/补助", prompt: "我要报出差费用" },
  { key: "meal", label: "吃饭/招待", hint: "餐饮/客户招待", prompt: "我要报餐饮或招待费用" },
  { key: "transport", label: "打车/高铁", hint: "市内交通/城际", prompt: "我要报交通费用" },
  { key: "supplement", label: "补充发票", hint: "漏了票/挂到哪张单", prompt: "我要补充发票" },
  { key: "over-budget", label: "超标怎么办", hint: "超标准/超预算", prompt: "我的报销超标了，怎么办" },
];

const emit = defineEmits<{ (e: "pick", prompt: string): void }>();
</script>

<template>
  <div class="ec-wrap">
    <div class="ec-title">你想办哪件事？</div>
    <div class="ec-grid">
      <button
        v-for="e in ENTRIES"
        :key="e.key"
        class="ec-item"
        type="button"
        @click="emit('pick', e.prompt)"
      >
        <span class="ec-label">{{ e.label }}</span>
        <span class="ec-hint">{{ e.hint }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.ec-wrap { padding: 8px 0; }
.ec-title { font-size: 13px; color: var(--c-sub, #8c8c8c); margin-bottom: 8px; }
.ec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
}
.ec-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
  padding: 10px 12px;
  border: 1px solid #e6e8eb;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  text-align: left;
}
.ec-item:hover { border-color: #1677ff; }
.ec-label { font-size: 13px; font-weight: 600; color: #1f2937; }
.ec-hint { font-size: 12px; color: var(--c-sub, #8c8c8c); }
</style>
