<!-- 图表渲染：chart-* fence 的 ECharts 真图（按需引入，不含 parseChartSpec——父组件解析好传入）
     支持「展开查看」：放大到全屏遮罩大图（Esc / 点击遮罩 / ✕ 关闭） -->
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart, PieChart, RadarChart } from "echarts/charts";
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ChartKind, ChartSpec } from "../chartSpec";

echarts.use([PieChart, BarChart, RadarChart, GridComponent, TooltipComponent, LegendComponent, TitleComponent, CanvasRenderer]);

const props = defineProps<{
  kind: ChartKind;
  spec: ChartSpec;
  loading?: boolean;
}>();

const el = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

// ==== 展开查看（全屏大图） ====
const expanded = ref(false);
const bigEl = ref<HTMLDivElement | null>(null);
let bigChart: echarts.ECharts | null = null;

async function openExpanded() {
  expanded.value = true;
  await nextTick(); // 等遮罩与容器挂载后再 init
  if (!bigEl.value) return;
  bigChart ??= echarts.init(bigEl.value);
  bigChart.setOption(buildOption(), true);
  bigChart.resize();
}

function closeExpanded() {
  bigChart?.dispose();
  bigChart = null;
  expanded.value = false;
}

function onEsc(e: KeyboardEvent) {
  if (e.key === "Escape") closeExpanded();
}

// 展开期间：Esc 关闭 + 锁定背景滚动
watch(expanded, (open) => {
  if (open) {
    window.addEventListener("keydown", onEsc);
    document.body.style.overflow = "hidden";
  } else {
    window.removeEventListener("keydown", onEsc);
    document.body.style.overflow = "";
  }
});

function buildOption(): echarts.EChartsCoreOption {
  const { kind, spec } = props;
  const title = spec.title ? { text: spec.title, left: "center", textStyle: { fontSize: 13 } } : undefined;
  const base = {
    title,
    tooltip: { trigger: kind === "pie" ? "item" : "axis" },
    legend: { bottom: 0, type: "scroll", textStyle: { fontSize: 11 } },
  };
  const grid = { left: 8, right: 8, top: title ? 34 : 12, bottom: 26, containLabel: true };

  if (kind === "pie") {
    return {
      ...base,
      series: [{ type: "pie", radius: ["40%", "70%"], center: ["50%", "45%"], data: spec.data ?? [] }],
    };
  }
  if (kind === "bar") {
    const data = spec.data ?? [];
    return {
      ...base,
      grid,
      xAxis: { type: "category", data: data.map((d) => d.name) },
      yAxis: { type: "value" },
      series: [{ type: "bar", data: data.map((d) => d.value) }],
    };
  }
  if (kind === "grouped-bar") {
    return {
      ...base,
      grid,
      xAxis: { type: "category", data: spec.categories ?? [] },
      yAxis: { type: "value" },
      series: (spec.series ?? []).map((s) => ({ type: "bar", name: s.name, data: s.values })),
    };
  }
  // radar：indicator max 取各列最大 ×1.1（全 0 列兜底 1，避免退化）
  const cats = spec.categories ?? [];
  const series = spec.series ?? [];
  const indicator = cats.map((name, i) => ({
    name,
    max: Math.max(1, ...series.map((s) => s.values[i] ?? 0)) * 1.1,
  }));
  return {
    ...base,
    radar: { indicator },
    series: [{ type: "radar", data: series.map((s) => ({ name: s.name, value: s.values })) }],
  };
}

function render() {
  if (!el.value || props.loading) return;
  chart ??= echarts.init(el.value);
  chart.setOption(buildOption(), true); // notMerge：换 kind/spec 时不留旧系列
}

function onResize() {
  chart?.resize();
  bigChart?.resize();
}

onMounted(() => {
  render();
  window.addEventListener("resize", onResize);
});
// flush: post —— 骨架切真图时容器 div 才刚渲染出来
watch(() => [props.kind, props.spec, props.loading], render, { deep: true, flush: "post" });
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  window.removeEventListener("keydown", onEsc);
  document.body.style.overflow = "";
  bigChart?.dispose();
  bigChart = null;
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div v-if="loading" class="chart-skeleton">图表生成中…</div>
  <div v-else class="chart-wrap">
    <div ref="el" class="chart-box"></div>
    <button class="expand-btn" type="button" aria-label="展开查看图表" title="展开查看" @click="openExpanded">
      ⤢ 展开
    </button>
  </div>
  <!-- 全屏大图：Teleport 到 body，避免被面板/消息列表的 overflow 裁剪 -->
  <Teleport to="body">
    <div v-if="expanded" class="chart-overlay" @click.self="closeExpanded">
      <div class="chart-panel">
        <header class="chart-panel-head">
          <span class="chart-panel-title">{{ spec.title || "图表" }}</span>
          <button class="chart-panel-close" type="button" aria-label="关闭" @click="closeExpanded">✕</button>
        </header>
        <div ref="bigEl" class="chart-big"></div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.chart-wrap {
  position: relative;
}
.chart-box {
  width: 100%;
  height: 260px;
}
/* 悬停图表时显示展开按钮（键盘 focus 同样可见） */
.expand-btn {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 2;
  padding: 2px 8px;
  font-size: 12px;
  color: #4b5563;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #e2e5ea;
  border-radius: 6px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s;
}
.chart-wrap:hover .expand-btn,
.expand-btn:focus-visible {
  opacity: 1;
}
.expand-btn:hover {
  color: #2563eb;
  border-color: #bfdbfe;
}
.chart-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000; /* 高于 antd 抽屉/弹层，确保全屏可见 */
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.45);
}
.chart-panel {
  display: flex;
  flex-direction: column;
  width: min(1080px, 92vw);
  height: min(680px, 84vh);
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.3);
  overflow: hidden;
}
.chart-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid #eef0f3;
  flex-shrink: 0;
}
.chart-panel-title {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}
.chart-panel-close {
  border: none;
  background: transparent;
  color: #8a94a6;
  font-size: 14px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
}
.chart-panel-close:hover {
  background: #f2f4f7;
  color: #374151;
}
.chart-big {
  flex: 1;
  min-height: 0; /* flex 子项允许收缩，ECharts 才能拿到正确高度 */
}
.chart-skeleton {
  height: 260px; /* 与 .chart-box 一致，骨架切真图不跳版 */
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f2f4f7;
  border: 1px dashed #d7dce5;
  border-radius: 8px;
  color: #8a94a6;
  font-size: 13px;
}
</style>
