<!-- 原件定位面板：页面渲染图 + 锚点高亮覆盖层
     结构约束（勿改）：高亮层必须与 img 同处于「图片尺寸包裹层」内。
     若把包裹层换成带 max-height 的滚动容器，绝对定位子元素的 top/height
     百分比将相对被截断的容器高度解析（实测高亮被压到 46%、整体上移）。 -->
<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  imageUrl: string;
  bbox: [number, number, number, number] | null;
}>();

/** 归一化 bbox（PDF 坐标，原点左下）→ CSS 定位（原点左上，y 需翻转） */
const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;

const highlightStyle = computed(() => {
  const b = props.bbox;
  if (!b) return null;
  const [x0, y0, x1, y1] = b;
  return {
    left: pct(x0),
    top: pct(1 - y1),
    width: pct(x1 - x0),
    height: pct(y1 - y0),
  };
});
</script>

<template>
  <div class="locate-scroll">
    <!-- 图片尺寸包裹层：宽 100%，高度由图片撑开（百分比坐标的定位基准） -->
    <div class="receipt-locate-frame">
      <img :src="imageUrl" class="frame-img" />
      <div v-if="highlightStyle" class="receipt-anchor-highlight" :style="highlightStyle"></div>
    </div>
  </div>
</template>

<style scoped>
/* 滚动容器：必须在图片尺寸包裹层之外（见上方结构约束） */
.locate-scroll {
  max-height: 70vh;
  overflow: auto;
}

.frame-img {
  width: 100%;
  display: block;
}

.receipt-locate-frame {
  position: relative;
  width: 100%;
}

/* 锚点高亮（浅色主题：半透明黄底 + 描边，不遮挡文字） */
.receipt-anchor-highlight {
  position: absolute;
  background: rgba(250, 219, 20, 0.35);
  border: 2px solid #d4b106;
  border-radius: 4px;
  pointer-events: none;
  box-shadow: 0 0 0 4000px rgba(0, 0, 0, 0.06);
}
</style>
