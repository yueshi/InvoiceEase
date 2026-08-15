<!-- 审计日志：筛选 / 分页 / 详情 JSON -->
<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { errorMessage } from "../api/client";
import { listAuditLogs } from "../api/audit";
import type { AuditListResponse } from "../types";

const data = ref<AuditListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
const filters = reactive({ action: undefined as string | undefined, dateRange: undefined as [string, string] | undefined });

async function load() {
  loading.value = true;
  try {
    data.value = await listAuditLogs({
      action: filters.action,
      date_from: filters.dateRange?.[0],
      date_to: filters.dateRange?.[1],
      page: data.value.page,
      page_size: data.value.page_size,
    });
  } catch (e) {
    errorMessage(e, "审计日志加载失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const columns = [
  { title: "时间", dataIndex: "created_at", key: "created_at" },
  { title: "操作", dataIndex: "action", key: "action" },
  { title: "用户", dataIndex: "user_id", key: "user_id" },
  { title: "发票", dataIndex: "invoice_id", key: "invoice_id" },
  { title: "通道", dataIndex: "channel", key: "channel" },
  { title: "详情", dataIndex: "detail", key: "detail" },
];
</script>

<template>
  <div>
    <h3>审计日志</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-select v-model:value="filters.action" placeholder="操作类型" allow-clear style="width: 180px" @change="load">
        <a-select-option v-for="a in ['FETCH','PARSE','VERIFY','REVIEW','LOGIN','LOGOUT','REJECT_REPLY','CONFIG_CHANGE','REVERIFY']" :key="a" :value="a">{{ a }}</a-select-option>
      </a-select>
      <a-range-picker v-model:value="filters.dateRange" @change="load" />
      <a-button type="primary" @click="load">查询</a-button>
    </a-space>
    <a-table :columns="columns" :data-source="data.items" :loading="loading" row-key="id"
      :pagination="{ total: data.total, current: data.page, pageSize: data.page_size }"
      @change="(p: any) => { data.page = p.current; data.page_size = p.pageSize; load(); }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'detail'">
          <span style="font-size: 12px; color: #888">{{ JSON.stringify(record.detail) }}</span>
        </template>
      </template>
    </a-table>
  </div>
</template>
