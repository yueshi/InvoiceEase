<!-- 助手消息渲染：md 段走 markdown、chart-* fence 走 ECharts、其它 fence 走代码块；
     流式时最后一段只在「安全点」前渲染 markdown，余量按纯文本展示（防半开语法闪烁） -->
<script setup lang="ts">
import { computed, defineAsyncComponent } from "vue";
import { flushablePrefix, splitSegments, type Segment } from "../streamFlusher";
import { parseChartSpec, type ChartKind, type ChartSpec } from "../chartSpec";
import { parseAnomalyCard, parseDraftCard, type AnomalyCard, type DraftCard } from "../claimCardSpec";
import { renderMarkdown } from "../markdown";
import ClaimDraftCard from "./ClaimDraftCard.vue";
import AnomalyCardView from "./AnomalyCard.vue";
// echarts 体积大（gzip ~184KB），异步分块：仅真出图时才拉取，不进首屏主 chunk
const AgentChart = defineAsyncComponent(() => import("./AgentChart.vue"));

const props = defineProps<{
  text: string;
  streaming?: boolean;
}>();

interface RenderSeg extends Segment {
  html?: string;
  tail?: string;
  chart?: { kind: ChartKind; spec: ChartSpec } | null;
  draft?: DraftCard | null;
  anomaly?: AnomalyCard | null;
}

const emit = defineEmits<{ (e: "chat-action", prompt: string): void }>();

const CHART_KINDS: ChartKind[] = ["pie", "bar", "grouped-bar", "radar"];
/** 骨架用：从 lang 推 kind，认不出时按柱图占位 */
function kindFromLang(lang: string): ChartKind {
  const k = lang.replace(/^chart-/, "") as ChartKind;
  return CHART_KINDS.includes(k) ? k : "bar";
}
const isChartFence = (lang: string) => lang.startsWith("chart-");

const segments = computed<RenderSeg[]>(() => {
  const segs = splitSegments(props.text);
  return segs.map((seg, i) => {
    const isTail = !!props.streaming && i === segs.length - 1;
    if (seg.kind === "md") {
      const body = isTail ? flushablePrefix(seg.content) : seg.content;
      return { ...seg, html: renderMarkdown(body), tail: isTail ? seg.content.slice(body.length) : "" };
    }
    return {
      ...seg,
      chart: isChartFence(seg.lang) ? parseChartSpec(seg.lang, seg.content) : null,
      draft: parseDraftCard(seg.lang, seg.content),
      anomaly: parseAnomalyCard(seg.lang, seg.content),
    };
  });
});
</script>

<template>
  <div class="md-bubble">
    <template v-for="(seg, i) in segments" :key="i">
      <div v-if="seg.kind === 'md'" class="md-body">
        <div v-html="seg.html" />
        <span v-if="seg.tail" class="md-tail">{{ seg.tail }}</span>
      </div>
      <AgentChart v-else-if="seg.chart" :kind="seg.chart.kind" :spec="seg.chart.spec" />
      <AgentChart
        v-else-if="isChartFence(seg.lang) && !seg.closed && streaming"
        loading
        :kind="kindFromLang(seg.lang)"
        :spec="{}"
      />
      <template v-else-if="isChartFence(seg.lang)">
        <div class="chart-fallback">图表数据不完整</div>
        <pre class="code-block"><code>{{ seg.content }}</code></pre>
      </template>
      <ClaimDraftCard
        v-else-if="seg.draft"
        :card="seg.draft"
        @submit="(p: string) => emit('chat-action', p)"
      />
      <AnomalyCardView
        v-else-if="seg.anomaly"
        :card="seg.anomaly"
        @choose="(p: string) => emit('chat-action', p)"
      />
      <pre v-else class="code-block"><code>{{ seg.content }}</code></pre>
    </template>
    <span v-if="streaming" class="cursor">▍</span>
  </div>
</template>

<style scoped>
.md-bubble {
  max-width: 92%;
  padding: 8px 12px;
  border-radius: 8px;
  background: #f2f4f7;
  color: #1f2937;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}
/* 未 flush 的余量：纯文本展示，等下一安全点再转 markdown */
.md-tail {
  white-space: pre-wrap;
}
.cursor {
  animation: blink 1s step-start infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
.md-body :deep(p),
.md-body :deep(ul),
.md-body :deep(ol),
.md-body :deep(blockquote) {
  margin: 0 0 6px;
}
.md-body :deep(> div > :last-child) {
  margin-bottom: 0;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 20px;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  font-size: 14px;
  margin: 6px 0 4px;
}
.md-body :deep(code) {
  background: #e3e7ee;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 12px;
}
.md-body :deep(pre) {
  background: #fafbfc;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 6px 8px;
  margin: 4px 0 6px;
  overflow-x: auto;
}
.md-body :deep(pre code) {
  background: none;
  padding: 0;
}
.md-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 4px 0 6px;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #d7dce5;
  padding: 3px 8px;
  text-align: left;
}
.md-body :deep(th) {
  background: #e9edf4;
}
.md-body :deep(blockquote) {
  padding-left: 10px;
  border-left: 3px solid #cbd5e1;
  color: #4b5563;
}
.md-body :deep(a) {
  color: #2563eb;
}
.code-block {
  background: #fafbfc;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 6px 8px;
  margin: 4px 0 6px;
  font-size: 12px;
  overflow-x: auto;
  white-space: pre-wrap;
}
.chart-fallback {
  color: #8a94a6;
  font-size: 12px;
  margin-top: 4px;
}
</style>
