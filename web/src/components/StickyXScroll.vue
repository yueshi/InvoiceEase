<!-- 吸底横向滚动条：表格保持自然高度（页面滚动），本组件在视口底部常驻一条
     细横向滚动条，与所在卡片内 .ant-table-content 双向同步 scrollLeft。
     解决「表格很长时横向滚动条在表格最底部、鼠标用户够不到」的问题，
     且不引入表格固定高度。放在 .table-card 内、</a-table> 之后即可（自动找父级）。

     实现要点：滚动事件用**捕获阶段委托**挂在稳定的父卡片上（scroll 不冒泡但可捕获），
     每次同步/测量都重新 querySelector——antd 在 loading→数据 切换时可能替换
     表格内部节点，直接持有旧引用会静默失效（实测踩过）。 -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

const bar = ref<HTMLDivElement | null>(null);
const contentWidth = ref(0);
const windowWidth = ref(0);
let parent: HTMLElement | null = null;
let syncing = false;
let ro: ResizeObserver | null = null;

function findContent(): HTMLElement | null {
  return parent?.querySelector<HTMLElement>(".ant-table-content") ?? null;
}

/** 防回环：双向同步时用 rAF 释放锁 */
function guard(fn: () => void) {
  if (syncing) return;
  syncing = true;
  fn();
  requestAnimationFrame(() => (syncing = false));
}

function measure() {
  const c = findContent();
  if (!c) return;
  contentWidth.value = c.scrollWidth;
  windowWidth.value = c.clientWidth;
}

/** 捕获阶段收到卡片内任意元素的 scroll：只处理表格容器 */
function onAnyScroll(e: Event) {
  const target = e.target as HTMLElement;
  if (!target?.classList?.contains("ant-table-content")) return;
  guard(() => {
    if (bar.value) bar.value.scrollLeft = target.scrollLeft;
  });
}

function fromBar() {
  const c = findContent();
  guard(() => {
    if (bar.value && c) c.scrollLeft = bar.value.scrollLeft;
  });
}

onMounted(() => {
  parent = (bar.value?.parentElement as HTMLElement) ?? null;
  if (!parent) return;
  parent.addEventListener("scroll", onAnyScroll, { capture: true, passive: true });
  measure();
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(measure);
    ro.observe(parent);
  }
  window.addEventListener("resize", measure);
});

onBeforeUnmount(() => {
  parent?.removeEventListener("scroll", onAnyScroll, { capture: true });
  window.removeEventListener("resize", measure);
  ro?.disconnect();
  ro = null;
});
</script>

<template>
  <!-- 仅当表格真的横向溢出时显示；宽度=表格滚动宽度，滚动条本身在视口底部吸住 -->
  <div
    v-show="contentWidth > windowWidth + 1"
    ref="bar"
    class="sticky-xscroll"
    aria-hidden="true"
    @scroll.passive="fromBar"
  >
    <div class="sticky-xscroll-inner" :style="{ width: contentWidth + 'px' }" />
  </div>
</template>

<style scoped>
.sticky-xscroll {
  position: sticky;
  bottom: 0;
  z-index: 2;
  overflow-x: auto;
  background: #fff;
  border-top: 1px solid #eef0f3;
}
.sticky-xscroll-inner {
  height: 1px;
}
/* 与表格容器滚动条一致的 8px 风格，避免原生浮层滚动条不可见（macOS） */
.sticky-xscroll::-webkit-scrollbar {
  height: 8px;
}
.sticky-xscroll::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}
.sticky-xscroll::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}
.sticky-xscroll::-webkit-scrollbar-track {
  background: #f1f5f9;
}
</style>
