<!-- 工作台：统计卡片 -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { errorMessage } from "../api/client";
import { fetchOverview } from "../api/stats";
import type { StatsOverviewOut } from "../types";

const stats = ref<StatsOverviewOut | null>(null);

onMounted(async () => {
  try {
    stats.value = await fetchOverview();
  } catch (e) {
    errorMessage(e, "统计加载失败");
  }
});
</script>

<template>
  <div>
    <h3>工作台</h3>
    <a-row :gutter="16">
      <a-col :span="6"><a-card><a-statistic title="待复核" :value="stats?.pending_review ?? 0" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="待提交" :value="stats?.pending_submit ?? 0" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="今日新增" :value="stats?.today_new ?? 0" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="本月累计" :value="stats?.month_total ?? 0" /></a-card></a-col>
    </a-row>
  </div>
</template>
