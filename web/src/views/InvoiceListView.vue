<!-- 发票列表：筛选 / 分页 / 详情 / 复核 / 重验 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs, { type Dayjs } from "dayjs";
import { message } from "ant-design-vue";
import { downloadFile, errorMessage } from "../api/client";
import { listInvoices, reVerify, reviewInvoice, uploadInvoice } from "../api/invoices";
import { useAuthStore } from "../stores/auth";
import { INVOICE_STATUS_LABELS, VERIFY_STATUS_LABELS, type InvoiceListResponse, type InvoiceOut } from "../types";
import InvoiceDetailDrawer from "../components/InvoiceDetailDrawer.vue";
import PreviewModal from "../components/PreviewModal.vue";

const auth = useAuthStore();
const data = ref<InvoiceListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
// 周期过滤：全部 / 按月 / 按季度 / 按年 —— 单一锚点驱动，切换类型保持上下文
// （历史缺陷：各选择器独立且默认"今天"，从「年 2026」切到「按季度」会跳回当前季，
//  加上选择器不可清空，用户找不到非当前周期的发票；见 2026-09-13 排查）
const filters = reactive({ status: undefined as string | undefined, keyword: "" });
const periodType = ref<"all" | "month" | "quarter" | "year">("month");
const anchor = ref<Dayjs>(dayjs()); // 唯一时间锚点：类型切换按它换算，选择器改动回写它

/** 锚点 → 当前周期的选择器值（clone，避免 dayjs 的 .month() 原地修改污染锚点） */
const month = computed<Dayjs>(() => anchor.value.clone().startOf("month"));
const quarter = computed<Dayjs>(() => anchor.value.clone().startOf("quarter"));
const year = computed<Dayjs>(() => anchor.value.clone().startOf("year"));

function onAnchorChange(d: Dayjs | null) {
  if (d) anchor.value = d;
  data.value.page = 1;
  load();
}

/** 切换周期类型：保持上下文（年 2026 → 该锚点所在季/月），而非回到"今天" */
function onPeriodTypeChange() {
  data.value.page = 1;
  load();
}

function periodRange(): { date_from?: string; date_to?: string } {
  if (periodType.value === "all") return {}; // 全部时间（后端支持不带日期）
  const a = anchor.value;
  if (periodType.value === "month") {
    return {
      date_from: a.startOf("month").format("YYYY-MM-DD"),
      date_to: a.endOf("month").format("YYYY-MM-DD"),
    };
  }
  if (periodType.value === "quarter") {
    const start = a.startOf("quarter");
    return {
      date_from: start.format("YYYY-MM-DD"),
      date_to: start.add(2, "month").endOf("month").format("YYYY-MM-DD"),
    };
  }
  return {
    date_from: a.startOf("year").format("YYYY-MM-DD"),
    date_to: a.endOf("year").format("YYYY-MM-DD"),
  };
}
const drawerOpen = ref(false);
const current = ref<InvoiceOut | null>(null);
const previewOpen = ref(false);
const previewTarget = ref<InvoiceOut | null>(null);

function exportMonthly() {
  const month = new Date().toISOString().slice(0, 7);
  downloadFile(`/reports/monthly/export?month=${month}`, `cost-${month}.xlsx`);
}

async function onBeforeUpload(file: File) {
  // 员工交票（M3）：返回 false 阻止 a-upload 默认行为，手动走 API 后刷新列表
  try {
    await uploadInvoice(file);
    message.success("已上传，系统自动解析中");
    await load();
  } catch (e) {
    errorMessage(e);
  }
  return false;
}

function showPreview(record: InvoiceOut) {
  previewTarget.value = record;
  previewOpen.value = true;
}

const canReview = () => ["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "");

// 周期外匹配探测：筛选条件命中的发票若都在当前周期外，给出"查看全部"入口
// （历史痛点：用户按发票号搜索却因周期过滤看不到，以为数据丢了）
const outsidePeriodCount = ref(0);

async function probeOutsidePeriod() {
  const hasFilter = Boolean(filters.keyword || filters.status);
  if (periodType.value === "all" || !hasFilter || data.value.total > 0) {
    outsidePeriodCount.value = 0;
    return;
  }
  try {
    const all = await listInvoices({
      status: filters.status,
      keyword: filters.keyword || undefined,
      page: 1,
      page_size: 1,
    });
    outsidePeriodCount.value = all.total;
  } catch {
    outsidePeriodCount.value = 0;
  }
}

function showAllPeriods() {
  periodType.value = "all";
  outsidePeriodCount.value = 0;
  reloadFirst();
}

async function load() {
  loading.value = true;
  try {
    data.value = await listInvoices({
      status: filters.status,
      keyword: filters.keyword || undefined,
      ...periodRange(),
      page: data.value.page,
      page_size: data.value.page_size,
    });
  } catch (e) {
    errorMessage(e, "列表加载失败");
  } finally {
    loading.value = false;
  }
  probeOutsidePeriod();
}

function onPageChange(page: number, pageSize: number) {
  data.value.page = page;
  data.value.page_size = pageSize;
  load();
}

function reloadFirst() {
  data.value.page = 1;
  load();
}

function showDetail(record: InvoiceOut) {
  current.value = record;
  drawerOpen.value = true;
}

async function onReview(record: InvoiceOut, action: "approve" | "reject") {
  try {
    await reviewInvoice(record.id, action);
    message.success(action === "approve" ? "已通过，进入待提交" : "已驳回");
    load();
  } catch (e) {
    errorMessage(e, "复核操作失败");
  }
}

async function onReVerify(record: InvoiceOut) {
  try {
    await reVerify(record.id);
    message.success("已重新触发验真");
    load();
  } catch (e) {
    errorMessage(e, "重验失败");
  }
}

onMounted(load);

const columns = [
  { title: "发票号码", dataIndex: "invoice_number", key: "invoice_number" },
  { title: "销售方", dataIndex: "seller_name", key: "seller_name" },
  { title: "开票日期", dataIndex: "issue_date", key: "issue_date" },
  { title: "价税合计", dataIndex: "total_amount", key: "total_amount" },
  { title: "状态", dataIndex: "status", key: "status" },
  { title: "验真", dataIndex: "verify_status", key: "verify_status" },
  { title: "操作", key: "actions" },
];
</script>

<template>
  <div>
    <h3>发票列表</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-select v-model:value="filters.status" placeholder="状态" allow-clear style="width: 160px" @change="reloadFirst">
        <a-select-option v-for="(label, value) in INVOICE_STATUS_LABELS" :key="value" :value="value">{{ label }}</a-select-option>
      </a-select>
      <a-input v-model:value="filters.keyword" placeholder="发票号码/购销方" style="width: 220px" @press-enter="reloadFirst" />
      <a-radio-group v-model:value="periodType" button-style="solid" @change="onPeriodTypeChange">
        <a-radio-button value="all">全部</a-radio-button>
        <a-radio-button value="month">按月</a-radio-button>
        <a-radio-button value="quarter">按季度</a-radio-button>
        <a-radio-button value="year">按年</a-radio-button>
      </a-radio-group>
      <a-month-picker
        v-if="periodType === 'month'"
        :value="month"
        :allow-clear="false"
        @change="onAnchorChange"
      />
      <a-date-picker
        v-else-if="periodType === 'quarter'"
        :value="quarter"
        picker="quarter"
        :allow-clear="false"
        @change="onAnchorChange"
      />
      <a-date-picker
        v-else-if="periodType === 'year'"
        :value="year"
        picker="year"
        :allow-clear="false"
        @change="onAnchorChange"
      />
      <a-button type="primary" @click="reloadFirst">查询</a-button>
      <a-button @click="exportMonthly">导出本月台账</a-button>
      <a-upload :before-upload="onBeforeUpload" :show-upload-list="false" accept=".pdf,.ofd,.xml">
        <a-button>上传发票</a-button>
      </a-upload>
    </a-space>
    <a-alert
      v-if="!loading && data.total === 0 && outsidePeriodCount > 0"
      type="info"
      show-icon
      style="margin-bottom: 12px"
      :message="`当前周期（${periodType === 'month' ? month.format('YYYY-MM') : periodType === 'quarter' ? quarter.format('YYYY-[Q]Q') : year.format('YYYY')}）内无匹配，但其他周期有 ${outsidePeriodCount} 条`"
    >
      <template #description>
        目标发票可能开票日期在其他周期 ——
        <a @click="showAllPeriods">查看全部时间</a>
      </template>
    </a-alert>
    <p v-else-if="!loading && data.total === 0" style="color: #888; margin-bottom: 8px">
      当前筛选无发票；可切换周期或选「全部」（不做日期过滤）。
    </p>
    <a-table :columns="columns" :data-source="data.items" :loading="loading" row-key="id"
      :pagination="{ total: data.total, current: data.page, pageSize: data.page_size, showSizeChanger: true }"
      @change="(p: any) => onPageChange(p.current, p.pageSize)">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="record.status === 'pending_review' ? 'orange' : record.status === 'blocked' ? 'red' : 'blue'">
            {{ INVOICE_STATUS_LABELS[record.status] || record.status }}
          </a-tag>
          <!-- 报销维度（与业务状态正交）：已报销 / 报销中 -->
          <a-tag v-if="record.reimbursement_status === 'claimed'" color="green" style="margin-left: 4px">已报销</a-tag>
          <a-tag v-else-if="record.reimbursement_status === 'pending'" color="gold" style="margin-left: 4px">报销中</a-tag>
        </template>
        <template v-else-if="column.key === 'verify_status'">
          {{ VERIFY_STATUS_LABELS[record.verify_status] || record.verify_status }}
        </template>
        <template v-else-if="column.key === 'invoice_number' || column.key === 'seller_name' || column.key === 'issue_date' || column.key === 'total_amount'">
          <!-- 待复核/解析失败记录无结构化字段，显示占位符而非空白 -->
          <span :style="record[column.dataIndex] ? {} : { color: '#bbb' }">{{ record[column.dataIndex] || "—" }}</span>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a @click="showDetail(record)">详情</a>
            <a @click="showPreview(record)">预览</a>
            <template v-if="canReview() && record.status === 'pending_review'">
              <a @click="onReview(record, 'approve')">通过</a>
              <a @click="onReview(record, 'reject')">驳回</a>
            </template>
            <a v-if="canReview()" @click="onReVerify(record)">重验</a>
          </a-space>
        </template>
      </template>
    </a-table>
    <InvoiceDetailDrawer v-model:open="drawerOpen" :invoice="current" @refresh="load" />
    <PreviewModal v-model:open="previewOpen" :invoice="previewTarget" />
  </div>
</template>
