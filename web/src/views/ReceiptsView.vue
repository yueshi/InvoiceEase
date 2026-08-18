<!-- 银行回单：上传/列表/配对/无票筛选/凭证草稿导出（P3/R1-R2，财务角色） -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import { autoPairReceipt, exportReceipts, listReceipts, uploadReceipt } from "../api/receipts";
import type { ReceiptOut } from "../types";

const month = ref(new Date().toISOString().slice(0, 7));
const unmatchedOnly = ref(false);
const rows = ref<ReceiptOut[]>([]);
const loading = ref(false);

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
    rows.value = await listReceipts(month.value, unmatchedOnly.value);
  } catch (e) {
    errorMessage(e, "回单加载失败");
  } finally {
    loading.value = false;
  }
}

async function onBeforeUpload(file: File) {
  try {
    const r = await uploadReceipt(file);
    message.success(
      r.amount
        ? `已解析：${r.counterparty_name || "未知对方"} ${r.amount} 元（${r.status === "paired" ? `已配对发票 #${r.paired_invoice_id}` : "未配对"}）`
        : "已上传（解析字段缺失，请人工补充）",
    );
    await load();
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
  exportReceipts(month.value).catch((e) => message.error(errorMessage(e)));
}

onMounted(load);
</script>

<template>
  <div>
    <h3>银行回单</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-month-picker v-model:value="month" :allow-clear="false" @change="load" />
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
          <a-button size="small" @click="onAutoPair(record)">自动配对</a-button>
        </template>
      </template>
    </a-table>
  </div>
</template>
