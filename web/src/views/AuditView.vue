<!-- 审计日志：筛选 / 分页 / 详情 JSON -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import type { Dayjs } from "dayjs";
import { errorMessage } from "../api/client";
import { listAuditLogs } from "../api/audit";
import dayjs from "dayjs";
import { AUDIT_OUTCOME_LABELS, type AuditListResponse, type AuditOut } from "../types";
import PageHeader from "../components/PageHeader.vue";

const data = ref<AuditListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
// 视图分类：业务审计（默认，排除身份事件）/ 安全审计（登录成败/登出，等保要求）
const category = ref<"business" | "security">("business");
// 详情弹窗：原始 JSON 直接内联会撑爆表格（删除快照等很长）→ 列表只显示摘要
const detailOpen = ref(false);
const detailRecord = ref<AuditOut | null>(null);

const SUMMARY_MAX = 48;

/** 详情摘要：键=值 拼接并限长——列表只做"提示"，完整内容进弹窗（原始 JSON 会撑爆表格） */
function detailSummary(detail: Record<string, unknown> | null): string {
  if (!detail) return "—";
  const text = Object.entries(detail)
    .map(([k, v]) => `${k}=${v !== null && typeof v === "object" ? "…" : String(v)}`)
    .join(" · ");
  return text.length > SUMMARY_MAX ? text.slice(0, SUMMARY_MAX) + "…" : text;
}

function openDetail(record: AuditOut) {
  detailRecord.value = record;
  detailOpen.value = true;
}
const filters = reactive({ action: undefined as string | undefined, dateRange: undefined as [Dayjs, Dayjs] | undefined });
// 安全审计默认只看失败：成功登录量大、日常无异常含义，需要时一键看全部
const failuresOnly = ref(true);
// 业务视图：「仅看异常」（拦截/失败/系统错误）——审计的核心用例是找异常
const abnormalOnly = ref(false);

const BUSINESS_ACTIONS = ["FETCH", "PARSE", "VERIFY", "REVIEW", "RECEIPT_REVIEW", "RECEIPT_CATEGORY", "REJECT_REPLY", "CONFIG_CHANGE", "REVERIFY", "INGEST", "INVOICE_UPDATE", "INVOICE_DELETE", "UNBLOCK"];
const SECURITY_ACTIONS = ["LOGIN", "LOGIN_FAILED", "LOGOUT"];

function onCategoryChange() {
  filters.action = undefined; // 分类切换后清掉上一个分类的操作类型
  failuresOnly.value = category.value === "security"; // 安全视图默认只看失败
  reloadFirst();
}

async function load() {
  loading.value = true;
  try {
    data.value = await listAuditLogs({
      category: category.value,
      action: filters.action || (category.value === "security" && failuresOnly.value ? "LOGIN_FAILED" : undefined),
      outcome: category.value === "business" && abnormalOnly.value ? "abnormal" : undefined,
      date_from: filters.dateRange?.[0]?.format("YYYY-MM-DD"),
      date_to: filters.dateRange?.[1]?.format("YYYY-MM-DDT23:59:59"),
      page: data.value.page,
      page_size: data.value.page_size,
    });
  } catch (e) {
    errorMessage(e, "审计日志加载失败");
  } finally {
    loading.value = false;
  }
}

function reloadFirst() {
  data.value.page = 1;
  load();
}
onMounted(load);

// 列宽约束：无 width 的列会被表格拉伸吃掉空间（曾致「结果」横跨 400+px、时间列被挤换行）
const BASE_COLUMNS = [
  { title: "时间", dataIndex: "created_at", key: "created_at", width: 190 },
  { title: "操作", dataIndex: "action", key: "action", width: 150 },
  { title: "用户", dataIndex: "user_id", key: "user_id", width: 70 },
  { title: "发票", dataIndex: "invoice_id", key: "invoice_id", width: 70 },
  { title: "通道", dataIndex: "channel", key: "channel", width: 80 },
  { title: "详情", key: "detail", width: 260 },
];

// 「结果」列两个视图都显示（审计要素：事件是否成功）——数据源为落库的 outcome
const columns = computed(() => [
  BASE_COLUMNS[0],
  BASE_COLUMNS[1],
  { title: "结果", key: "result", width: 80 },
  ...BASE_COLUMNS.slice(2),
]);
</script>

<template>
  <div>
    <PageHeader title="审计日志" :desc="category === 'business' ? '业务操作留痕（收信/解析/验真/复核/配置变更等）；身份事件在「安全审计」标签页。' : '身份事件（登录成功/失败/登出）——失败登录是撞库与爆破检测依据，长期保留。'" />
    <a-tabs v-model:active-key="category" @change="onCategoryChange">
      <a-tab-pane key="business" tab="业务审计" />
      <a-tab-pane key="security" tab="安全审计" />
    </a-tabs>
    <div class="filter-toolbar">
      <a-select v-model:value="filters.action" placeholder="操作类型" allow-clear style="width: 180px" @change="reloadFirst">
        <a-select-option v-for="a in (category === 'security' ? SECURITY_ACTIONS : BUSINESS_ACTIONS)" :key="a" :value="a">{{ a }}</a-select-option>
      </a-select>
      <a-checkbox v-if="category === 'security'" v-model:checked="failuresOnly" @change="reloadFirst">
        仅看失败登录
      </a-checkbox>
      <a-checkbox v-else v-model:checked="abnormalOnly" @change="load">
        仅看异常
      </a-checkbox>
      <a-range-picker v-model:value="filters.dateRange" @change="reloadFirst" />
      <a-button type="primary" @click="load">查询</a-button>
    </div>
    <div class="table-card">
    <a-table :columns="columns" :data-source="data.items" :loading="loading" row-key="id" :scroll="{ x: 960 }"
      :pagination="{ total: data.total, current: data.page, pageSize: data.page_size }"
      @change="(p: any) => { data.page = p.current; data.page_size = p.pageSize; load(); }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'created_at'">
          {{ dayjs(record.created_at).format("YYYY-MM-DD HH:mm:ss") }}
        </template>
        <template v-else-if="column.key === 'result'">
          <a-tooltip v-if="AUDIT_OUTCOME_LABELS[record.outcome || '']" :title="record.action === 'PARSE' && record.outcome === 'blocked' ? '业务处置（重复拦截/非发票拒收），非失败' : ''">
            <a-tag :color="AUDIT_OUTCOME_LABELS[record.outcome || ''].color">
              {{ AUDIT_OUTCOME_LABELS[record.outcome || ''].text }}
            </a-tag>
          </a-tooltip>
          <span v-else>—</span>
        </template>
        <template v-if="column.key === 'detail'">
          <div class="audit-detail-cell">
            <span class="audit-detail-text" :title="detailSummary(record.detail)">{{ detailSummary(record.detail) }}</span>
            <a v-if="record.detail" @click="openDetail(record)">查看</a>
          </div>
        </template>
      </template>
    </a-table>
    </div>
    <a-modal v-model:open="detailOpen" title="审计详情" width="720px" :footer="null">
      <a-descriptions :column="1" size="small" bordered class="mb-3">
        <a-descriptions-item label="时间">{{ detailRecord?.created_at }}</a-descriptions-item>
        <a-descriptions-item label="操作">{{ detailRecord?.action }}</a-descriptions-item>
        <a-descriptions-item label="用户 / 通道">
          {{ detailRecord?.user_id ?? "—" }} / {{ detailRecord?.channel }}
        </a-descriptions-item>
        <a-descriptions-item v-if="detailRecord?.ip_address" label="IP">
          {{ detailRecord.ip_address }}
        </a-descriptions-item>
      </a-descriptions>
      <pre class="detail-json">{{ JSON.stringify(detailRecord?.detail ?? {}, null, 2) }}</pre>
    </a-modal>
  </div>
</template>

<style scoped>
/* 详情列：硬截断 + 查看入口（flex 容器内 antd Typography 的 ellipsis 测量不可靠，
   直接用 CSS 截断，长 JSON 不再撑开表格） */
.audit-detail-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  overflow: hidden;
}
.audit-detail-cell > a {
  flex: 0 0 auto; /* 不参与收缩，避免「查看」被挤成两行 */
  white-space: nowrap;
}
.audit-detail-text {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 12px;
  color: var(--c-sub);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.detail-json {
  max-height: 55vh;
  overflow: auto;
  background: #fafafa;
  border-radius: 4px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
}
.mb-3 { margin-bottom: var(--space-3); }
</style>
