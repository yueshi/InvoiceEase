<!-- 图表渲染：chart-* fence 的 ECharts 真图（按需引入，不含 parseChartSpec——父组件解析好传入） -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
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
}

onMounted(() => {
  render();
  window.addEventListener("resize", onResize);
});
// flush: post —— 骨架切真图时容器 div 才刚渲染出来
watch(() => [props.kind, props.spec, props.loading], render, { deep: true, flush: "post" });
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div v-if="loading" class="chart-skeleton">图表生成中…</div>
  <div v-else ref="el" class="chart-box"></div>
</template>

<style scoped>
.chart-box {
  width: 100%;
  height: 260px;
}
.chart-skeleton {
  height: 200px;
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
