<!-- 银行回单：上传/列表/配对/无票筛选/凭证草稿导出（P3/R1-R2，财务角色） -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs, { type Dayjs } from "dayjs";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import {
  autoPairReceipt,
  confirmReceiptReview,
  exportReceipts,
  fetchReceiptFileUrl,
  fetchReceiptPageUrl,
  listReceiptUploads,
  listReceipts,
  probeReceiptsPeriod,
  uploadReceipt,
} from "../api/receipts";
import { BANK_LABELS, type ReceiptOut } from "../types";
import PageHeader from "../components/PageHeader.vue";
import { formatMoney } from "../utils/format";
import ReceiptDetailDrawer from "../components/ReceiptDetailDrawer.vue";
import ReceiptLocatePanel from "../components/ReceiptLocatePanel.vue";

// a-month-picker / a-date-picker 的 value 必须是 dayjs 对象（组件内部会调 .locale()）
const periodType = ref<"all" | "month" | "quarter" | "year">("month");
const month = ref<Dayjs>(dayjs());
const quarter = ref<Dayjs>(dayjs());
const year = ref<Dayjs>(dayjs());
const unmatchedOnly = ref(false);
const reviewOnly = ref(false); // 只看待核对（质量标记：空户名/账号残留/本司账户行等）
const rows = ref<ReceiptOut[]>([]);
const loading = ref(false);
const displayRows = computed(() =>
  reviewOnly.value ? rows.value.filter((r) => r.needs_review) : rows.value,
);
/** 季度标签 YYYY-QN（手算避免依赖 dayjs quarterOfYear 插件格式符） */
function quarterLabel(d: Dayjs): string {
  return `${d.year()}-Q${Math.floor(d.month() / 3) + 1}`;
}

function periodParam(): { month?: string; quarter?: string; year?: string } {
  if (periodType.value === "all") return {}; // 全部时间（后端不做日期过滤）
  if (periodType.value === "month") return { month: month.value.format("YYYY-MM") };
  if (periodType.value === "quarter") return { quarter: quarterLabel(quarter.value) };
  return { year: year.value.format("YYYY") };
}

// 质量问题不占列（细节收进详情抽屉），状态列的「待核对」标签 + 只看待核对筛选足够暴露
const columns = [
  { title: "交易日期", dataIndex: "trade_date", key: "trade_date", width: 110 },
  { title: "银行", dataIndex: "bank_code", key: "bank_code", width: 90 },
  { title: "对方户名", dataIndex: "counterparty_name", key: "counterparty_name" },
  { title: "金额", dataIndex: "amount", key: "amount", width: 110, align: "right" as const },
  { title: "收付", dataIndex: "direction", key: "direction", width: 60 },
  { title: "摘要", dataIndex: "abstract", key: "abstract" },
  { title: "发票配对", dataIndex: "paired_invoice_id", key: "paired_invoice_id" },
  { title: "状态", dataIndex: "status", key: "status" },
  { title: "操作", key: "action" },
];

/** 状态文案/配色：未配对行按「是否需要发票」区分（税费/社保等标无需发票，不进催票语义） */
function statusText(r: ReceiptOut): string {
  if (r.status === "paired") return "已配对";
  if (r.status === "unmatched") return r.invoice_requirement === "fetch" ? "无票" : "无需发票";
  return "待处理";
}
function statusColor(r: ReceiptOut): string {
  if (r.status === "paired") return "green";
  if (r.status === "unmatched") return r.invoice_requirement === "fetch" ? "orange" : "default";
  return "blue";
}

// 详情抽屉
const detailOpen = ref(false);
const detailRecord = ref<ReceiptOut | null>(null);

function onDetail(r: ReceiptOut) {
  detailRecord.value = r;
  detailOpen.value = true;
}

// 周期外提示：当前周期查不到时，告诉用户"全部时间还有 N 条"
// （历史痛点：回单页默认「本月」，跨月补录的回单看不见也无提示，被当成上传失败）
const outsidePeriodCount = ref(0);

async function probeOutsidePeriod() {
  if (periodType.value === "all" || rows.value.length > 0) {
    outsidePeriodCount.value = 0;
    return;
  }
  try {
    outsidePeriodCount.value = await probeReceiptsPeriod(unmatchedOnly.value);
  } catch {
    outsidePeriodCount.value = 0;
  }
}

function showAllPeriods() {
  periodType.value = "all";
  outsidePeriodCount.value = 0;
  load();
}

/** 当前周期标签（提示文案用） */
const periodLabel = computed(() => {
  if (periodType.value === "month") return month.value.format("YYYY-MM");
  if (periodType.value === "quarter") return quarterLabel(quarter.value);
  return year.value.format("YYYY");
});

async function load() {
  loading.value = true;
  try {
    rows.value = await listReceipts(periodParam(), unmatchedOnly.value);
  } catch (e) {
    errorMessage(e, "回单加载失败");
  } finally {
    loading.value = false;
  }
  probeOutsidePeriod();
}

async function onViewFile(r: ReceiptOut) {
  try {
    const url = await fetchReceiptFileUrl(r.id);
    window.open(url, "_blank");
  } catch (e) {
    errorMessage(e, "原件打开失败");
  }
}

// 原件定位弹窗：所在页渲染图 + 锚点高亮覆盖层；渲染不可用则降级打开原 PDF 第 N 页
const locateOpen = ref(false);
const locateRecord = ref<ReceiptOut | null>(null);
const locateImageUrl = ref("");
const locateLoading = ref(false);

async function onLocate(r: ReceiptOut) {
  locateRecord.value = r;
  locateImageUrl.value = "";
  locateOpen.value = true;
  locateLoading.value = true;
  try {
    locateImageUrl.value = await fetchReceiptPageUrl(r.id);
  } catch {
    // 501（无渲染库）或其它错误 → 降级：直接打开原 PDF 对应页
    locateOpen.value = false;
    message.warning(`页面渲染不可用，已改为打开原 PDF 第 ${r.page_no ?? 1} 页`);
    await onViewFileAtPage(r);
  } finally {
    locateLoading.value = false;
  }
}

async function onViewFileAtPage(r: ReceiptOut) {
  try {
    const url = await fetchReceiptFileUrl(r.id);
    window.open(`${url}#page=${r.page_no ?? 1}&view=FitH`, "_blank");
  } catch (e) {
    errorMessage(e, "原件打开失败");
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
    errorMessage(e);
  }
  return false;
}

async function onConfirmReview(r: ReceiptOut) {
  try {
    await confirmReceiptReview(r.id);
    message.success("已标记核对无误，移出待核对队列");
    await load();
  } catch (e) {
    errorMessage(e, "核对操作失败");
  }
}

async function onAutoPair(r: ReceiptOut) {
  try {
    const updated = await autoPairReceipt(r.id);
    message.success(updated.paired_invoice_id ? `已配对发票 #${updated.paired_invoice_id}` : "未找到匹配发票");
    await load();
  } catch (e) {
    errorMessage(e);
  }
}

function onExport() {
  exportReceipts(periodParam()).catch((e) => errorMessage(e));
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader title="银行回单" desc="回单上传、解析与发票配对；支持凭证草稿导出。">
      <template #extra>
        <a-upload :before-upload="onBeforeUpload" :show-upload-list="false" accept=".pdf,.png,.jpg,.jpeg">
          <a-button type="primary">上传回单</a-button>
        </a-upload>
        <a-button @click="onExport">导出凭证草稿</a-button>
        <a-button @click="load">刷新</a-button>
      </template>
    </PageHeader>
    <div class="filter-toolbar">
      <a-radio-group v-model:value="periodType" button-style="solid" @change="load">
        <a-radio-button value="all">全部</a-radio-button>
        <a-radio-button value="month">按月</a-radio-button>
        <a-radio-button value="quarter">按季度</a-radio-button>
        <a-radio-button value="year">按年</a-radio-button>
      </a-radio-group>
      <a-month-picker v-if="periodType === 'month'" v-model:value="month" :allow-clear="false" @change="load" />
      <a-date-picker
        v-else-if="periodType === 'quarter'"
        v-model:value="quarter"
        picker="quarter"
        :allow-clear="false"
        @change="load"
      />
      <a-date-picker v-else v-model:value="year" picker="year" :allow-clear="false" @change="load" />
      <a-checkbox v-model:checked="unmatchedOnly" @change="load">只看无票支出</a-checkbox>
      <a-checkbox v-model:checked="reviewOnly">只看待核对</a-checkbox>
    </div>
    <a-alert
      v-if="!loading && rows.length === 0 && outsidePeriodCount > 0"
      type="info"
      show-icon
      class="mb-3"
      :message="`当前周期（${periodLabel}）内无回单，但其他周期有 ${outsidePeriodCount} 条`"
    >
      <template #description>
        回单的交易日期可能在其他周期（如跨月补录）——
        <a @click="showAllPeriods">查看全部时间</a>
      </template>
    </a-alert>
    <a-empty v-else-if="!loading && rows.length === 0" description="当前筛选无回单；可切换周期或选「全部」（不做日期过滤）。" />
    <a-empty v-else-if="!loading && displayRows.length === 0" :description="`当前周期有 ${rows.length} 条回单，但都被「只看待核对」筛掉了。`" />
    <div class="table-card">
    <a-table
      :columns="columns"
      :data-source="displayRows"
      :loading="loading"
      row-key="id"
      :pagination="{ pageSize: 20 }"
      :row-class-name="(r: ReceiptOut) => (r.needs_review ? 'receipt-review-row' : '')"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'bank_code'">
          {{ record.bank_code ? (BANK_LABELS[record.bank_code] || record.bank_code) : "—" }}
        </template>
        <template v-if="column.key === 'direction'">
          {{ { 收: '收', 付: '付' }[record.direction as '收' | '付'] || '—' }}
        </template>
        <template v-if="column.key === 'amount'">
          <span class="num">{{ formatMoney(record.amount) }}</span>
        </template>
        <template v-if="column.key === 'status'">
          <a-tag :color="statusColor(record)">{{ statusText(record) }}</a-tag>
          <a-tooltip v-if="record.needs_review" title="解析质量存疑，请核对原件">
            <a-tag color="red" class="gap-tag">待核对</a-tag>
          </a-tooltip>
        </template>
        <template v-if="column.key === 'action'">
          <a-space>
            <a @click="onDetail(record)">详情</a>
            <a @click="onLocate(record)">定位{{ record.page_no ? ` P${record.page_no}` : "" }}</a>
            <a @click="onAutoPair(record)">自动配对</a>
            <a v-if="record.needs_review" class="warn-text" @click="onConfirmReview(record)">核对无误</a>
          </a-space>
        </template>
      </template>
    </a-table>
    </div>
    <ReceiptDetailDrawer
      v-model:open="detailOpen"
      :receipt="detailRecord"
      @view-file="onViewFile"
      @view-page="onViewFileAtPage"
      @locate="onLocate"
      @refresh="load"
    />
    <a-modal
      v-model:open="locateOpen"
      :title="`原件定位${locateRecord?.page_no ? `（第 ${locateRecord.page_no} 页）` : ''}`"
      width="860px"
      :footer="null"
    >
      <a-spin :spinning="locateLoading">
        <ReceiptLocatePanel
          v-if="locateImageUrl"
          :image-url="locateImageUrl"
          :bbox="locateRecord?.anchor?.bbox ?? null"
        />
        <a-alert
          v-if="locateRecord && !locateRecord.anchor?.bbox"
          type="info"
          show-icon
          class="mt-2"
          message="本张回单未能定位到页内精确区域（仅定位到页码），请在本页人工核对。"
        />
        <a-space class="mt-3">
          <a-button @click="locateRecord && onViewFileAtPage(locateRecord)">打开原 PDF 该页</a-button>
        </a-space>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
/* 行内静态样式归位（值一一对应，零视觉变化）：间距走全局刻度令牌 */
.mb-3 {
  margin-bottom: var(--space-3);
}
.gap-tag {
  margin-left: var(--space-1);
}
.mt-2 {
  margin-top: var(--space-2);
}
.mt-3 {
  margin-top: var(--space-3);
}
.warn-text {
  color: var(--c-warn); /* 待核对操作入口与行高亮同色系，扫视时成组 */
}

/* 待核对行高亮（--c-danger 浅底） */
:deep(.receipt-review-row) > td {
  background: #fef2f2;
}
</style>
