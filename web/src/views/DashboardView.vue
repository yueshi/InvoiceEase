<!-- 工作台：统计卡片 + 开票抬头卡片 -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import { listCompanyInfos } from "../api/companyInfos";
import { fetchOverview, fetchTrustStats } from "../api/stats";
import type { CompanyInfoOut, StatsOverviewOut, TrustStatsOut } from "../types";

const stats = ref<StatsOverviewOut | null>(null);
const trust = ref<TrustStatsOut | null>(null);
const selfInfo = ref<CompanyInfoOut | null>(null);

async function loadTrust() {
  try {
    trust.value = await fetchTrustStats();
  } catch {
    // 员工无 finance 权限时静默（信任卡片仅财务可见）
  }
}

async function loadSelfInfo() {
  try {
    const items = await listCompanyInfos();
    const def = items.find((i) => i.kind === "self" && i.is_default);
    selfInfo.value = def ?? items.find((i) => i.kind === "self") ?? null;
  } catch (e) {
    errorMessage(e, "抬头加载失败");
  }
}

async function copyHeader() {
  if (!selfInfo.value) return;
  try {
    await navigator.clipboard.writeText(`${selfInfo.value.name} ${selfInfo.value.tax_id}`);
    message.success("已复制开票抬头");
  } catch {
    message.error("复制失败，请手动选择");
  }
}

onMounted(async () => {
  try {
    stats.value = await fetchOverview();
  } catch (e) {
    errorMessage(e, "统计加载失败");
  }
  await loadSelfInfo();
  await loadTrust();
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
    <a-card v-if="trust" title="数字员工信任（近 7 天）" size="small" style="margin-top: 16px">
      <a-row :gutter="16">
        <a-col :span="6"><a-statistic title="自动处理" :value="trust.auto_count" /></a-col>
        <a-col :span="6"><a-statistic title="人工复核" :value="trust.manual_count" /></a-col>
        <a-col :span="6"><a-statistic title="人工改判" :value="trust.overturn_count" /></a-col>
        <a-col :span="6">
          <a-statistic title="改判率" :value="Math.round(trust.overturn_rate * 100)" suffix="%" />
        </a-col>
      </a-row>
    </a-card>
    <a-card title="开票抬头" size="small" style="margin-top: 16px">
      <template v-if="selfInfo">
        <p style="margin: 0 0 8px">{{ selfInfo.name }}</p>
        <p style="margin: 0 0 8px; color: #888">税号：{{ selfInfo.tax_id }}</p>
        <a-button size="small" @click="copyHeader">复制抬头</a-button>
      </template>
      <p v-else style="margin: 0; color: #888">尚未配置开票抬头（请联系管理员在「公司信息」中设置）</p>
    </a-card>
  </div>
</template>
