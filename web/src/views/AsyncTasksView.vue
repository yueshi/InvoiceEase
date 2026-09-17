<!-- 异步任务（批量）：回单上传批次解析进度（R1.1，财务角色） -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import dayjs from "dayjs";
import { errorMessage } from "../api/client";
import { listReceiptUploads } from "../api/receipts";
import type { ReceiptUploadOut } from "../types";
import PageHeader from "../components/PageHeader.vue";

const rows = ref<ReceiptUploadOut[]>([]);
const loading = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;

const columns = [
  { title: "批次", dataIndex: "id", key: "id", width: 70 },
  { title: "状态", key: "status", width: 100 },
  { title: "入库张数", dataIndex: "receipt_count", key: "receipt_count", width: 90 },
  { title: "上传时间", key: "created_at" },
  { title: "完成时间", key: "parsed_at" },
  { title: "失败原因", dataIndex: "error", key: "error", ellipsis: true },
];

const STATUS_META: Record<string, { text: string; color: string }> = {
  parsing: { text: "解析中", color: "blue" },
  parsed: { text: "完成", color: "green" },
  failed: { text: "失败", color: "red" },
};

async function load() {
  loading.value = true;
  try {
    rows.value = await listReceiptUploads();
  } catch (e) {
    errorMessage(e, "任务列表加载失败");
  } finally {
    loading.value = false;
    schedule();
  }
}

// 有解析中的批次时自动轮询（5s），全部结束后停止
function schedule() {
  if (timer) clearTimeout(timer);
  if (rows.value.some((r) => r.status === "parsing")) {
    timer = setTimeout(load, 5000);
  }
}

onMounted(load);
onUnmounted(() => timer && clearTimeout(timer));
</script>

<template>
  <div>
    <PageHeader
      title="异步任务"
      desc="批量上传（如一份 PDF 含多张回单）在后台解析；此处查看解析进度与入库结果，解析中的任务每 5 秒自动刷新。"
    >
      <template #extra>
        <a-button @click="load">刷新</a-button>
      </template>
    </PageHeader>
    <div class="table-card">
      <a-table
      :columns="columns"
      :data-source="rows"
      :loading="loading"
      row-key="id"
      :pagination="{ pageSize: 20 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="STATUS_META[record.status]?.color || 'default'">
            {{ STATUS_META[record.status]?.text || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'created_at'">
          {{ record.created_at ? dayjs(record.created_at).format("YYYY-MM-DD HH:mm:ss") : "—" }}
        </template>
        <template v-else-if="column.key === 'parsed_at'">
          {{ record.parsed_at ? dayjs(record.parsed_at).format("YYYY-MM-DD HH:mm:ss") : "—" }}
        </template>
      </template>
    </a-table>
    </div>
  </div>
</template>
