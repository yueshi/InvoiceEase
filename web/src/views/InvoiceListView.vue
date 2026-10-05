<!-- 发票列表：筛选 / 分页 / 详情 / 复核 / 重验 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs, { type Dayjs } from "dayjs";
import { message } from "ant-design-vue";
import { useRoute } from "vue-router";
import { downloadFile, errorMessage } from "../api/client";
import {
  getInvoice,
  importSalesInvoices,
  importSalesList,
  linkOriginalInvoice,
  listInvoices,
  listUnlinkedRed,
  reVerify,
  reviewInvoice,
  updateInvoice,
  uploadInvoice,
  type UnlinkedRedInvoice,
} from "../api/invoices";
import { useAuthStore } from "../stores/auth";
import { EXPENSE_TYPE_COLORS, EXPENSE_TYPE_LABELS, INVOICE_STATUS_LABELS, VERIFY_STATUS_LABELS, type InvoiceListResponse, type InvoiceOut } from "../types";
import PageHeader from "../components/PageHeader.vue";
import StickyXScroll from "../components/StickyXScroll.vue";
import InvoiceDetailDrawer from "../components/InvoiceDetailDrawer.vue";
import PreviewModal from "../components/PreviewModal.vue";
import { formatMoney } from "../utils/format";

const auth = useAuthStore();
const data = ref<InvoiceListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
// 周期过滤：全部 / 按月 / 按季度 / 按年 —— 单一锚点驱动，切换类型保持上下文
// （历史缺陷：各选择器独立且默认"今天"，从「年 2026」切到「按季度」会跳回当前季，
//  加上选择器不可清空，用户找不到非当前周期的发票；见 2026-09-13 排查）
const filters = reactive({
  status: undefined as string | undefined,
  keyword: "",
  expense_type: undefined as string | undefined, // unclassified = 未归类
  invoice_direction: undefined as string | undefined, // input 进项 / output 销项
});
// 默认「全部」：裸进页面即见全量数据（此前默认本月，跨月数据看不见被误当数据丢失）
const periodType = ref<"all" | "month" | "quarter" | "year">("all");
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

// ---- 已开票导入（销项）与红票关联 ----

const unlinkedRed = ref<UnlinkedRedInvoice[]>([]);

async function loadUnlinkedRed() {
  if (!["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "")) return;
  try {
    unlinkedRed.value = await listUnlinkedRed();
  } catch {
    // 非财务角色或接口不可用时静默（不打断主列表）
  }
}

async function onBeforeImportSales(file: File) {
  try {
    const { results } = await importSalesInvoices([file]);
    const r = results[0] as { status: string; invoice_number?: string; error?: string };
    if (r.status === "imported") message.success(`已导入销项票 ${r.invoice_number}`);
    else if (r.status === "skipped") message.warning(`已存在，跳过：${r.invoice_number || ""}`);
    else message.error(r.error || "导入失败");
    await load();
    await loadUnlinkedRed();
  } catch (e) {
    errorMessage(e, "导入失败");
  }
  return false;
}

async function onBeforeImportSalesList(file: File) {
  try {
    const r = await importSalesList(file);
    message.success(`清单导入：成功 ${r.imported}、跳过 ${r.skipped}、错误 ${r.errors}`);
    await load();
    await loadUnlinkedRed();
  } catch (e) {
    errorMessage(e, "清单导入失败");
  }
  return false;
}

async function onLinkRed(r: UnlinkedRedInvoice) {
  const input = window.prompt(`红票 #${r.id} 关联的原蓝票 ID（可在发票列表按号码查到 ID 列）`);
  if (!input) return;
  const originalId = Number(input);
  if (!Number.isFinite(originalId)) {
    message.warning("请输入数字 ID");
    return;
  }
  try {
    await linkOriginalInvoice(r.id, originalId);
    message.success("已关联原蓝票");
    await loadUnlinkedRed();
    await load();
  } catch (e) {
    errorMessage(e, "关联失败");
  }
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
      expense_type: filters.expense_type,
      invoice_direction: filters.invoice_direction,
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

// 行内归类：点标签记录目标行 → 菜单选择类型（避免模板内联箭头/类型注解）
const typeTarget = ref<InvoiceOut | null>(null);

function onTypeMenuClick(info: { key: string }) {
  if (typeTarget.value) onSetExpenseType(typeTarget.value, info.key);
}

async function onSetExpenseType(record: InvoiceOut, expenseType: string) {
  try {
    await updateInvoice(record.id, { expense_type: expenseType });
    message.success(`已归类为「${EXPENSE_TYPE_LABELS[expenseType] || expenseType}」`);
    load();
  } catch (e) {
    errorMessage(e, "归类失败");
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

const route = useRoute();

/** Agent 深链（design/2026-10-03）：链接是「定位型」——缺省全部时间；页面默认同为「全部」，口径一致。
 *  `route?.`：组件测试裸挂载（无 router 插件）时 useRoute() 为 undefined，取空参走默认视图。 */
function initFromQuery() {
  const q = route?.query ?? {};
  const hasBiz = ["status", "keyword", "expense_type", "invoice_direction", "period", "invoice_id"]
    .some((k) => q[k] !== undefined);
  if (!hasBiz) return;
  if (typeof q.status === "string") filters.status = q.status;
  if (typeof q.keyword === "string") filters.keyword = q.keyword;
  if (typeof q.expense_type === "string") filters.expense_type = q.expense_type;
  if (typeof q.invoice_direction === "string") filters.invoice_direction = q.invoice_direction;
  const period = typeof q.period === "string" ? q.period : "";
  const asMonth = period.match(/^(\d{4})-(\d{2})$/);
  const asQuarter = period.match(/^(\d{4})-Q([1-4])$/);
  if (asMonth) {
    periodType.value = "month";
    anchor.value = dayjs(`${period}-01`);
  } else if (asQuarter) {
    periodType.value = "quarter";
    const startMonth = String((Number(asQuarter[2]) - 1) * 3 + 1).padStart(2, "0");
    anchor.value = dayjs(`${asQuarter[1]}-${startMonth}-01`);
  } else if (/^\d{4}$/.test(period)) {
    periodType.value = "year";
    anchor.value = dayjs(`${period}-01-01`);
  } else {
    periodType.value = "all";
  }
}

onMounted(async () => {
  initFromQuery();
  load();
  loadUnlinkedRed();
  const rawId = route?.query?.invoice_id;
  const invoiceId = typeof rawId === "string" ? Number(rawId) : NaN;
  if (Number.isFinite(invoiceId) && invoiceId > 0) {
    try {
      current.value = await getInvoice(invoiceId);
      drawerOpen.value = true;
    } catch (e) {
      errorMessage(e, "发票加载失败");
    }
  }
});

// 注意：ellipsis 会让 antd 启用 table-layout: fixed——此时**每一列都必须有显式 width**，
// 否则窄容器（小窗口/助手面板挤压）下无宽度列会被压成 0px（列标题消失、内容溢出重叠）。
// 全列定宽后，容器不足时表格整体横向滚动（global.css 已配滚动条常显），不再塌缩。
const columns = [
  // 单号/日期/金额为原子值：ellipsis（内含 nowrap）防折行；号码加宽到 210 容纳 20 位数字
  { title: "发票号码", dataIndex: "invoice_number", key: "invoice_number", width: 210, ellipsis: true },
  { title: "方向", key: "invoice_direction", width: 80 },
  { title: "销售方", dataIndex: "seller_name", key: "seller_name", width: 200, ellipsis: true },
  { title: "提交人", dataIndex: "submitted_by_name", key: "submitted_by_name", width: 100, ellipsis: true },
  { title: "开票日期", dataIndex: "issue_date", key: "issue_date", width: 120, ellipsis: true },
  { title: "价税合计", dataIndex: "total_amount", key: "total_amount", width: 120, align: "right" as const, ellipsis: true },
  { title: "费用类型", key: "expense_type", width: 110 },
  { title: "状态", dataIndex: "status", key: "status", width: 110 },
  { title: "验真", dataIndex: "verify_status", key: "verify_status", width: 90 },
  { title: "操作", key: "actions", width: 170 },
];
</script>

<template>
  <div>
    <PageHeader title="发票列表" desc="收取的进项/销项发票：按周期与状态筛选、复核、归类与导出。">
      <template #extra>
        <a-upload :before-upload="onBeforeUpload" :show-upload-list="false" accept=".pdf,.ofd,.xml">
          <a-button>上传发票</a-button>
        </a-upload>
        <a-upload :before-upload="onBeforeImportSales" :show-upload-list="false" accept=".xml,.ofd,.pdf" multiple>
          <a-button>导入已开票</a-button>
        </a-upload>
        <a-upload :before-upload="onBeforeImportSalesList" :show-upload-list="false" accept=".csv,.xlsx,.xlsm">
          <a-button>导入开票清单</a-button>
        </a-upload>
        <a-button @click="exportMonthly">导出本月台账</a-button>
      </template>
    </PageHeader>
    <div class="filter-toolbar">
      <a-select v-model:value="filters.status" placeholder="状态" allow-clear style="width: 160px" @change="reloadFirst">
        <a-select-option v-for="(label, value) in INVOICE_STATUS_LABELS" :key="value" :value="value">{{ label }}</a-select-option>
      </a-select>
      <a-input v-model:value="filters.keyword" placeholder="发票号码/购销方" style="width: 220px" @press-enter="reloadFirst" />
      <a-select v-model:value="filters.invoice_direction" placeholder="方向" allow-clear style="width: 110px" @change="reloadFirst">
        <a-select-option value="input">进项</a-select-option>
        <a-select-option value="output">销项</a-select-option>
      </a-select>
      <a-select v-model:value="filters.expense_type" placeholder="费用类型" allow-clear style="width: 140px" @change="reloadFirst">
        <a-select-option v-for="(label, k) in EXPENSE_TYPE_LABELS" :key="k" :value="k">{{ label }}</a-select-option>
        <a-select-option value="unclassified">未归类</a-select-option>
      </a-select>
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
    </div>
    <a-alert
      v-if="unlinkedRed.length"
      type="warning"
      show-icon
      class="mb-3"
      :message="`有 ${unlinkedRed.length} 张红字票未关联原蓝票（销项退款对账需要）`"
    >
      <template #description>
        <span v-for="r in unlinkedRed.slice(0, 5)" :key="r.id" class="mr-3">
          #{{ r.id }} {{ r.invoice_number || "无号码" }} {{ r.total_amount || "" }}
          <a @click="onLinkRed(r)">关联原蓝票</a>
        </span>
      </template>
    </a-alert>
    <a-alert
      v-if="!loading && data.total === 0 && outsidePeriodCount > 0"
      type="info"
      show-icon
      class="mb-3"
      :message="`当前周期（${periodType === 'month' ? month.format('YYYY-MM') : periodType === 'quarter' ? quarter.format('YYYY-[Q]Q') : year.format('YYYY')}）内无匹配，但其他周期有 ${outsidePeriodCount} 条`"
    >
      <template #description>
        目标发票可能开票日期在其他周期 ——
        <a @click="showAllPeriods">查看全部时间</a>
      </template>
    </a-alert>
    <a-empty v-else-if="!loading && data.total === 0" description="当前筛选无发票；可切换周期或选「全部」（不做日期过滤）。" />
    <!-- 无数据时不渲染空表（避免「No data 空表 + 滚动条」的噪音；加载中仍显示表格骨架） -->
    <div v-else class="table-card">
    <a-table size="middle" :columns="columns" :data-source="data.items" :loading="loading" row-key="id"
      :pagination="{ total: data.total, current: data.page, pageSize: data.page_size, showSizeChanger: true }"
      :scroll="{ x: 1310 }"
      @change="(p: any) => onPageChange(p.current, p.pageSize)">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="record.status === 'pending_review' ? 'orange' : record.status === 'blocked' ? 'red' : 'blue'">
            {{ INVOICE_STATUS_LABELS[record.status] || record.status }}
          </a-tag>
          <!-- 报销维度（与业务状态正交）：已报销 / 报销中 -->
          <a-tag v-if="record.reimbursement_status === 'claimed'" color="green" class="gap-tag">已报销</a-tag>
          <a-tag v-else-if="record.reimbursement_status === 'pending'" color="gold" class="gap-tag">报销中</a-tag>
        </template>
        <template v-else-if="column.key === 'invoice_direction'">
          <a-tag :color="record.invoice_direction === 'output' ? 'geekblue' : 'green'">
            {{ record.invoice_direction === "output" ? "销项" : "进项" }}
          </a-tag>
          <a-tooltip v-if="record.red_flag" :title="record.original_invoice_id ? `已关联原蓝票 #${record.original_invoice_id}` : '红字票未关联原蓝票'">
            <a-tag :color="record.original_invoice_id ? 'red' : 'volcano'" class="gap-tag">
              红字{{ record.original_invoice_id ? "" : "?" }}
            </a-tag>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'expense_type'">
          <a-dropdown :trigger="['click']">
            <a-tag
              :color="EXPENSE_TYPE_COLORS[record.expense_type as string] || 'default'"
              class="clickable"
              @click="typeTarget = record"
            >
              {{ record.expense_type ? (EXPENSE_TYPE_LABELS[record.expense_type] || record.expense_type) : "未归类" }}
            </a-tag>
            <template #overlay>
              <a-menu @click="onTypeMenuClick">
                <a-menu-item v-for="(label, k) in EXPENSE_TYPE_LABELS" :key="k">{{ label }}</a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </template>
        <template v-else-if="column.key === 'verify_status'">
          {{ VERIFY_STATUS_LABELS[record.verify_status] || record.verify_status }}
        </template>
        <template v-else-if="column.key === 'total_amount'">
          <span class="num">{{ formatMoney(record.total_amount) }}</span>
        </template>
        <template v-else-if="column.key === 'invoice_number' || column.key === 'seller_name' || column.key === 'issue_date' || column.key === 'submitted_by_name'">
          <!-- 待复核/解析失败记录无结构化字段，显示占位符而非空白；提交人为空（邮箱自动收取无归属）同此 -->
          <span class="num" :style="record[column.dataIndex] ? {} : { color: '#bbb' }">{{ record[column.dataIndex] || "—" }}</span>
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
    <StickyXScroll />
    </div>
    <InvoiceDetailDrawer v-model:open="drawerOpen" :invoice="current" @refresh="load" />
    <PreviewModal v-model:open="previewOpen" :invoice="previewTarget" />
  </div>
</template>

<style scoped>
/* 行内静态样式归位（值一一对应，零视觉变化）：间距走全局刻度令牌 */
.mb-3 {
  margin-bottom: var(--space-3);
}
.mr-3 {
  margin-right: var(--space-3);
}
.gap-tag {
  margin-left: var(--space-1);
}
.clickable {
  cursor: pointer;
}
</style>
