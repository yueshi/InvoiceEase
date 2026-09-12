<!-- 银行回单：上传/列表/配对/无票筛选/凭证草稿导出（P3/R1-R2，财务角色） -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import dayjs, { type Dayjs } from "dayjs";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import {
  autoPairReceipt,
  exportReceipts,
  fetchReceiptFileUrl,
  listReceiptUploads,
  listReceipts,
  uploadReceipt,
} from "../api/receipts";
import type { ReceiptOut } from "../types";

// a-month-picker / a-date-picker 的 value 必须是 dayjs 对象（组件内部会调 .locale()）
const periodType = ref<"month" | "quarter">("month");
const month = ref<Dayjs>(dayjs());
const quarter = ref<Dayjs>(dayjs());
const unmatchedOnly = ref(false);
const rows = ref<ReceiptOut[]>([]);
const loading = ref(false);

/** 季度标签 YYYY-QN（手算避免依赖 dayjs quarterOfYear 插件格式符） */
function quarterLabel(d: Dayjs): string {
  return `${d.year()}-Q${Math.floor(d.month() / 3) + 1}`;
}

function periodParam(): { month?: string; quarter?: string } {
  return periodType.value === "month"
    ? { month: month.value.format("YYYY-MM") }
    : { quarter: quarterLabel(quarter.value) };
}

const columns = [
  { title: "交易日期", dataIndex: "trade_date", key: "trade_date" },
  { title: "对方户名", dataIndex: "counterparty_name", key: "counterparty_name" },
  { title: "金额", dataIndex: "amount", key: "amount" },
  { title: "摘要", dataIndex: "abstract", key: "abstract" },
  { title: "发票配对", dataIndex: "paired_invoice_id", key: "paired_invoice_id" },
  { title: "状态", dataIndex: "status", key: "status" },
  { title: "操作", key: "action" },
];

async function load() {
  loading.value = true;
  try {
    rows.value = await listReceipts(periodParam(), unmatchedOnly.value);
  } catch (e) {
    errorMessage(e, "回单加载失败");
  } finally {
    loading.value = false;
  }
}

async function onViewFile(r: ReceiptOut) {
  try {
    const url = await fetchReceiptFileUrl(r.id);
    window.open(url, "_blank");
  } catch (e) {
    message.error(errorMessage(e, "原件打开失败"));
  }
}

async function onBeforeUpload(file: File) {
  try {
    // 异步解析（R1.1）：上传立即返回批次号，轮询批次状态直到解析完成
    const { upload_id } = await uploadReceipt(file);
    message.info("已接收，回单解析中（含 LLM 兜底，约需数十秒），完成后自动刷新");
    const started = Date.now();
    const poll = async () => {
      const uploads = await listReceiptUploads();
      const up = uploads.find((u) => u.id === upload_id);
      if (!up || up.status === "parsing") {
        if (Date.now() - started > 180_000) {
          message.warning("解析超时，请稍后手动刷新查看结果");
          return;
        }
        setTimeout(poll, 3000);
        return;
      }
      if (up.status === "failed") {
        message.error(up.error || "回单解析失败");
        return;
      }
      message.success(`解析完成：入库 ${up.receipt_count} 张回单`);
      await load();
    };
    setTimeout(poll, 3000);
  } catch (e) {
    message.error(errorMessage(e));
  }
  return false;
}

async function onAutoPair(r: ReceiptOut) {
  try {
    const updated = await autoPairReceipt(r.id);
    message.success(updated.paired_invoice_id ? `已配对发票 #${updated.paired_invoice_id}` : "未找到匹配发票");
    await load();
  } catch (e) {
    message.error(errorMessage(e));
  }
}

function onExport() {
  exportReceipts(periodParam()).catch((e) => message.error(errorMessage(e)));
}

onMounted(load);
</script>

<template>
  <div>
    <h3>银行回单</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-radio-group v-model:value="periodType" button-style="solid" @change="load">
        <a-radio-button value="month">按月</a-radio-button>
        <a-radio-button value="quarter">按季度</a-radio-button>
      </a-radio-group>
      <a-month-picker v-if="periodType === 'month'" v-model:value="month" :allow-clear="false" @change="load" />
      <a-date-picker v-else v-model:value="quarter" picker="quarter" :allow-clear="false" @change="load" />
      <a-checkbox v-model:checked="unmatchedOnly" @change="load">只看无票支出</a-checkbox>
      <a-button @click="load">刷新</a-button>
      <a-button @click="onExport">导出凭证草稿</a-button>
      <a-upload :before-upload="onBeforeUpload" :show-upload-list="false" accept=".pdf,.png,.jpg,.jpeg">
        <a-button type="primary">上传回单</a-button>
      </a-upload>
    </a-space>
    <a-table :columns="columns" :data-source="rows" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="record.status === 'paired' ? 'green' : record.status === 'unmatched' ? 'orange' : 'blue'">
            {{ { paired: '已配对', unmatched: '无票', pending: '待处理' }[record.status as 'paired' | 'unmatched' | 'pending'] || record.status }}
          </a-tag>
        </template>
        <template v-if="column.key === 'action'">
          <a-space>
            <a-button size="small" @click="onAutoPair(record)">自动配对</a-button>
            <a-button size="small" @click="onViewFile(record)">原件</a-button>
          </a-space>
        </template>
      </template>
    </a-table>
  </div>
</template>
