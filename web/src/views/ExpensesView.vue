<!-- 我的报销：员工建单/选票/提交；财务审批（同一页面按角色显示操作） -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import {
  addInvoiceToClaim,
  approveClaim,
  createClaim,
  createEntry,
  deleteClaim,
  eligibleInvoices,
  getClaim,
  getExpenseConfig,
  listClaims,
  rejectClaim,
  removeEntry,
  removeItem,
  submitClaim,
  withdrawClaim,
} from "../api/expenses";
import { useAuthStore } from "../stores/auth";
import {
  CLAIM_STATUS_LABELS,
  EXPENSE_TYPE_COLORS,
  EXPENSE_TYPE_LABELS,
  SCENE_FIELDS,
  TRAVEL_SUBTYPES,
  VOUCHER_TYPE_LABELS,
  allowanceAmount,
  type ClaimOut,
  type ClaimDetailOut,
  type EligibleInvoiceOut,
} from "../types";

const auth = useAuthStore();
const canFinance = () => ["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "");

const rows = ref<ClaimOut[]>([]);
const loading = ref(false);
const statusFilter = ref<string | undefined>(undefined);
const typeFilter = ref<string | undefined>(undefined);

const createOpen = ref(false);
const createForm = ref({ title: "", remark: "", claim_type: "travel" });

const detailOpen = ref(false);
const detail = ref<ClaimDetailOut | null>(null);
// 事项（费用明细行）：按类型动态显示场景字段
const entryOpen = ref(false);
const entryForm = reactive({
  entry_type: "travel", title: "", occurred_on: "", scene: {} as Record<string, string>, note: "",
});
// 差旅按子类（交通/住宿/市内交通/伙食补助/其他），其余类型用固定场景字段
const travelSubtype = ref("transport");
const sceneDefs = computed(() => {
  if (entryForm.entry_type === "travel") {
    return TRAVEL_SUBTYPES.find((t) => t.value === travelSubtype.value)?.fields || [];
  }
  return SCENE_FIELDS[entryForm.entry_type] || [];
});
// 差旅伙食补助：金额 = 天数 × 日标准（后端落成内部凭证，见 P1-3）
// 日标准默认取公司配置（「按规定标准发放」才符合不征个税的前提）
const allowanceStandard = ref(100);
async function loadExpenseConfig() {
  try {
    allowanceStandard.value = (await getExpenseConfig()).travel_allowance_daily_standard;
  } catch {
    /* 配置拉取失败不阻断：保留默认值，后端仍会按公司标准计算 */
  }
}

/** 补助金额实时预览（日标准留空 → 按公司标准，与后端兜底一致） */
const allowancePreview = computed(() =>
  allowanceAmount(entryForm.scene.days, entryForm.scene.daily_standard || allowanceStandard.value),
);

function onSubtypeChange() {
  entryForm.scene = {};
  // 选中「伙食补助」时预填公司标准，避免只填天数算不出钱
  if (travelSubtype.value === "allowance") {
    entryForm.scene.daily_standard = String(allowanceStandard.value);
  }
}

const entryTypeOptions = [
  { value: "travel", label: "差旅" },
  { value: "procurement", label: "采购" },
  { value: "entertainment", label: "招待" },
  { value: "office", label: "办公" },
  { value: "other", label: "其他" },
];

/** 当前"加票目标事项"（从某事项行点「加凭证」时记录） */
const targetEntryId = ref<number | null>(null);

function openEntryForm() {
  travelSubtype.value = "transport";
  // 事项类型默认跟随单据类型（如差旅单里默认加差旅事项）
  entryForm.entry_type = detail.value?.claim.claim_type || "travel";
  entryForm.title = "";
  entryForm.occurred_on = "";
  entryForm.scene = {};
  entryForm.note = "";
  entryOpen.value = true;
}

async function onCreateEntry() {
  if (!detail.value) return;
  if (!entryForm.title.trim()) {
    message.warning("请填写事项说明");
    return;
  }
  if (!entryForm.occurred_on) {
    message.warning("请选择费用发生日期");
    return;
  }
  try {
    await createEntry(detail.value.claim.id, {
      entry_type: entryForm.entry_type,
      title: entryForm.title.trim(),
      occurred_on: entryForm.occurred_on,
      scene_fields: entryForm.entry_type === "travel"
        ? { subtype: travelSubtype.value, ...entryForm.scene }
        : entryForm.scene,
      note: entryForm.note || undefined,
    });
    message.success("事项已添加");
    entryOpen.value = false;
    await openDetail(detail.value.claim);
  } catch (e) {
    errorMessage(e, "事项添加失败");
  }
}

async function onRemoveEntry(entryId: number) {
  if (!detail.value) return;
  try {
    await removeEntry(detail.value.claim.id, entryId);
    message.success("事项已删除（其凭证占用已释放）");
    await openDetail(detail.value.claim);
    await load();
  } catch (e) {
    errorMessage(e, "删除失败");
  }
}

const poolOpen = ref(false);
const pool = ref<EligibleInvoiceOut[]>([]);
const poolLoading = ref(false);
const poolSelection = ref<number[]>([]);
const poolExpenseType = ref("other");

const columns = [
  { title: "单号", dataIndex: "claim_no", key: "claim_no" },
  { title: "事由", dataIndex: "title", key: "title" },
  { title: "类型", key: "claim_type", width: 100 },
  { title: "金额", dataIndex: "total_amount", key: "total_amount", width: 110 },
  { title: "张数", dataIndex: "item_count", key: "item_count", width: 70 },
  { title: "状态", dataIndex: "status", key: "status", width: 110 },
  { title: "提交时间", dataIndex: "submitted_at", key: "submitted_at" },
  { title: "操作", key: "actions" },
];

async function load() {
  loading.value = true;
  try {
    rows.value = await listClaims(statusFilter.value, typeFilter.value);
  } catch (e) {
    errorMessage(e, "报销单加载失败");
  } finally {
    loading.value = false;
  }
}

async function onCreate() {
  if (!createForm.value.title.trim()) {
    message.warning("请填写报销事由");
    return;
  }
  try {
    const claim = await createClaim(
      createForm.value.title.trim(), createForm.value.remark || undefined, createForm.value.claim_type,
    );
    message.success(`已创建 ${claim.claim_no}（${EXPENSE_TYPE_LABELS[claim.claim_type] || claim.claim_type}）`);
    createOpen.value = false;
    createForm.value = { title: "", remark: "", claim_type: "travel" };
    await load();
    await openDetail(claim);
  } catch (e) {
    errorMessage(e, "创建失败");
  }
}

async function openDetail(claim: ClaimOut) {
  try {
    detail.value = await getClaim(claim.id);
    detailOpen.value = true;
  } catch (e) {
    errorMessage(e, "详情加载失败");
  }
}

async function openPool(entryId: number) {
  targetEntryId.value = entryId;
  poolLoading.value = true;
  poolSelection.value = [];
  try {
    pool.value = await eligibleInvoices();
    poolOpen.value = true;
  } catch (e) {
    errorMessage(e, "发票池加载失败");
  } finally {
    poolLoading.value = false;
  }
}

async function onAddSelected() {
  if (!detail.value || targetEntryId.value === null || poolSelection.value.length === 0) {
    message.warning("请先勾选发票");
    return;
  }
  let ok = 0;
  const errors: string[] = [];
  for (const id of poolSelection.value) {
    try {
      await addInvoiceToClaim(detail.value.claim.id, targetEntryId.value, id, poolExpenseType.value);
      ok += 1;
    } catch (e) {
      // errorMessage 自身会弹提示；这里只收集文本用于汇总（避免双重弹窗）
      const text = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      errors.push(text || "加入失败");
    }
  }
  if (ok) message.success(`已加入 ${ok} 张发票`);
  errors.forEach((t) => message.error(t));
  poolOpen.value = false;
  await openDetail(detail.value.claim);
  await load();
}

async function onRemove(itemId: number) {
  try {
    await removeItem(itemId);
    if (detail.value) await openDetail(detail.value.claim);
    await load();
  } catch (e) {
    errorMessage(e, "移除失败");
  }
}

async function onSubmit() {
  if (!detail.value) return;
  try {
    await submitClaim(detail.value.claim.id);
    message.success("已提交审批");
    await openDetail(detail.value.claim);
    await load();
  } catch (e) {
    errorMessage(e, "提交失败");
  }
}

async function onDelete(claim: ClaimOut) {
  if (!window.confirm(`确认删除报销单 ${claim.claim_no}？其发票占用将释放（可重新报销）。`)) return;
  try {
    await deleteClaim(claim.id);
    message.success("已删除，发票占用已释放");
    if (detailOpen.value && detail.value?.claim.id === claim.id) detailOpen.value = false;
    await load();
  } catch (e) {
    errorMessage(e, "删除失败");
  }
}

async function onWithdraw(claim: ClaimOut) {
  try {
    await withdrawClaim(claim.id);
    message.success("已撤回");
    await load();
    if (detailOpen.value && detail.value?.claim.id === claim.id) await openDetail(claim);
  } catch (e) {
    errorMessage(e, "撤回失败");
  }
}

async function onApprove(claim: ClaimOut) {
  try {
    await approveClaim(claim.id);
    message.success("已通过，发票标记为已报销");
    await load();
    if (detailOpen.value && detail.value?.claim.id === claim.id) await openDetail(claim);
  } catch (e) {
    errorMessage(e, "审批失败");
  }
}

async function onReject(claim: ClaimOut) {
  const reason = window.prompt("驳回理由（必填）");
  if (!reason) return;
  try {
    await rejectClaim(claim.id, reason);
    message.success("已驳回，发票占用已释放");
    await load();
    if (detailOpen.value && detail.value?.claim.id === claim.id) await openDetail(claim);
  } catch (e) {
    errorMessage(e, "驳回失败");
  }
}

const detailEditable = computed(() => detail.value?.claim.status === "draft");
const detailStatus = computed(() =>
  detail.value ? CLAIM_STATUS_LABELS[detail.value.claim.status] : undefined,
);

onMounted(() => {
  load();
  loadExpenseConfig();
});
</script>

<template>
  <div>
    <h3>我的报销</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-select v-model:value="statusFilter" placeholder="状态" allow-clear style="width: 160px" @change="load">
        <a-select-option v-for="(v, k) in CLAIM_STATUS_LABELS" :key="k" :value="k">{{ v.text }}</a-select-option>
      </a-select>
      <a-select v-model:value="typeFilter" placeholder="单据类型" allow-clear style="width: 140px" @change="load">
        <a-select-option v-for="(label, k) in EXPENSE_TYPE_LABELS" :key="k" :value="k">{{ label }}</a-select-option>
      </a-select>
      <a-button @click="load">刷新</a-button>
      <a-button type="primary" @click="createOpen = true">新建报销单</a-button>
    </a-space>

    <a-table :columns="columns" :data-source="rows" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'claim_type'">
          <a-tag :color="EXPENSE_TYPE_COLORS[record.claim_type as string] || 'default'">
            {{ EXPENSE_TYPE_LABELS[record.claim_type as string] || record.claim_type }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="CLAIM_STATUS_LABELS[record.status as string]?.color">
            {{ CLAIM_STATUS_LABELS[record.status as string]?.text || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'submitted_at'">
          {{ record.submitted_at ? String(record.submitted_at).replace("T", " ").slice(0, 19) : "—" }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a @click="openDetail(record)">详情</a>
            <template v-if="canFinance() && record.status === 'pending_approval'">
              <a @click="onApprove(record)">通过</a>
              <a @click="onReject(record)">驳回</a>
            </template>
            <a
              v-if="(record.status === 'draft' || record.status === 'pending_approval') && record.applicant_id === auth.user?.id"
              @click="onWithdraw(record)"
            >撤回</a>
            <a
              v-if="canFinance() || (record.status === 'draft' || record.status === 'withdrawn')"
              style="color: #cf1322"
              @click="onDelete(record)"
            >删除</a>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 新建 -->
    <a-modal v-model:open="createOpen" title="新建报销单" @ok="onCreate">
      <a-form layout="vertical">
        <a-form-item label="报销类型">
          <a-select v-model:value="createForm.claim_type">
            <a-select-option v-for="(label, k) in EXPENSE_TYPE_LABELS" :key="k" :value="k">{{ label }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="报销事由">
          <a-input v-model:value="createForm.title" placeholder="如：6 月差旅报销" />
        </a-form-item>
        <a-form-item label="备注">
          <a-input v-model:value="createForm.remark" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 详情 -->
    <a-drawer
      :open="detailOpen"
      :title="detail ? `${detail.claim.claim_no} · ${detail.claim.title}` : '报销单详情'"
      width="720"
      @close="detailOpen = false"
    >
      <template v-if="detail">
        <a-space style="margin-bottom: 12px" wrap>
          <a-tag :color="EXPENSE_TYPE_COLORS[detail.claim.claim_type as string] || 'default'">
            {{ EXPENSE_TYPE_LABELS[detail.claim.claim_type as string] || detail.claim.claim_type }}
          </a-tag>
          <a-tag :color="detailStatus?.color">{{ detailStatus?.text }}</a-tag>
          <span>合计 <b>{{ detail.claim.total_amount }}</b> 元 · {{ detail.items.length }} 条明细</span>
          <template v-if="detailEditable">
            <a-button size="small" type="primary" @click="openEntryForm">添加事项</a-button>
            <a-button size="small" @click="onSubmit">提交审批</a-button>
          </template>
          <template v-if="canFinance() && detail.claim.status === 'pending_approval'">
            <a-button size="small" type="primary" @click="onApprove(detail.claim)">通过</a-button>
            <a-button size="small" danger @click="onReject(detail.claim)">驳回</a-button>
          </template>
        </a-space>
        <a-alert
          v-if="detail.claim.rejected_reason"
          type="error"
          show-icon
          style="margin-bottom: 12px"
          :message="`驳回理由：${detail.claim.rejected_reason}`"
        />
        <p v-if="detail.entries.length === 0" style="color: #888">
          还没有事项：先「添加事项」（如「北京出差机票」），再在事项下加凭证。
        </p>
        <a-collapse v-else :default-active-key="detail.entries.map((e) => String(e.id))">
          <a-collapse-panel v-for="entry in detail.entries" :key="String(entry.id)">
            <template #header>
              <a-space>
                <a-tag color="blue">{{ EXPENSE_TYPE_LABELS[entry.entry_type] || entry.entry_type }}</a-tag>
                <a-tag v-if="entry.scene_fields?.subtype" color="cyan">
                  {{ TRAVEL_SUBTYPES.find((t) => t.value === entry.scene_fields?.subtype)?.label || entry.scene_fields.subtype }}
                </a-tag>
                <b>{{ entry.title }}</b>
                <span style="color: #888">
                  {{ entry.occurred_on || "无日期" }} · {{ entry.amount }} 元 · {{ entry.items.length }} 张凭证
                </span>
              </a-space>
            </template>
            <template v-if="entry.scene_fields && Object.keys(entry.scene_fields).length">
              <p style="color: #666; margin-bottom: 8px">
                <span v-for="f in (entry.entry_type === 'travel'
                  ? (TRAVEL_SUBTYPES.find((t) => t.value === entry.scene_fields?.subtype)?.fields || [])
                  : (SCENE_FIELDS[entry.entry_type] || []))" :key="f.key" style="margin-right: 12px">
                  {{ f.label }}：{{ entry.scene_fields[f.key] || "—" }}
                </span>
              </p>
            </template>
            <a-space style="margin-bottom: 8px">
              <a-button v-if="detailEditable" size="small" @click="openPool(entry.id)">加发票</a-button>
              <a-button v-if="detailEditable" size="small" danger @click="onRemoveEntry(entry.id)">删除事项</a-button>
            </a-space>
            <a-table
              :columns="[
                { title: '凭证类型', key: 'voucher_type', width: 130 },
                { title: '发票/回单', key: 'source' },
                { title: '金额', dataIndex: 'amount', key: 'amount', width: 100 },
                { title: '费用类型', key: 'expense_type', width: 90 },
                { title: '税前扣除', key: 'deductible', width: 150 },
                { title: '操作', key: 'op', width: 70 },
              ]"
              :data-source="entry.items"
              row-key="id"
              size="small"
              :pagination="false"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'voucher_type'">
                  {{ VOUCHER_TYPE_LABELS[record.voucher_type as string] || record.voucher_type }}
                </template>
                <template v-else-if="column.key === 'source'">
                  <span v-if="record.invoice_id">发票 #{{ record.invoice_id }}</span>
                  <span v-else-if="record.receipt_id">回单 #{{ record.receipt_id }}</span>
                  <span v-else-if="record.auto_rule">自动计算</span>
                  <span v-else>人工凭证</span>
                  <span v-if="record.note" style="color: #888">（{{ record.note }}）</span>
                </template>
                <template v-else-if="column.key === 'expense_type'">
                  {{ EXPENSE_TYPE_LABELS[record.expense_type as string] || record.expense_type }}
                </template>
                <template v-else-if="column.key === 'deductible'">
                  <a-tag v-if="record.deductible" color="green">可扣除</a-tag>
                  <a-tooltip v-else :title="record.deductible_note || ''">
                    <a-tag color="orange">不可扣除</a-tag>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'op'">
                  <a-tooltip v-if="record.auto_rule" title="由补助天数 × 日标准自动计算；改天数或标准即可">
                    <span style="color: #888">自动</span>
                  </a-tooltip>
                  <a v-else-if="detailEditable" @click="onRemove(record.id)">移除</a>
                </template>
              </template>
            </a-table>
          </a-collapse-panel>
        </a-collapse>
      </template>
    </a-drawer>

    <!-- 添加事项（按类型动态场景字段） -->
    <a-modal v-model:open="entryOpen" title="添加报销事项" @ok="onCreateEntry">
      <a-form layout="vertical">
        <a-form-item label="事项类型">
          <a-select v-model:value="entryForm.entry_type">
            <a-select-option v-for="o in entryTypeOptions" :key="o.value" :value="o.value">{{ o.label }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="事项说明">
          <a-input v-model:value="entryForm.title" placeholder="如：上海→北京 高铁票" />
        </a-form-item>
        <a-form-item label="费用发生日期">
          <a-date-picker v-model:value="entryForm.occurred_on" value-format="YYYY-MM-DD" style="width: 100%" />
        </a-form-item>
        <a-form-item v-if="entryForm.entry_type === 'travel'" label="差旅子类">
          <a-select v-model:value="travelSubtype" @change="onSubtypeChange">
            <a-select-option v-for="t in TRAVEL_SUBTYPES" :key="t.value" :value="t.value">
              {{ t.label }}
            </a-select-option>
          </a-select>
        </a-form-item>
        <template v-for="f in sceneDefs" :key="f.key">
          <a-form-item :label="f.label + (f.required ? '（必填）' : '')">
            <a-input v-model:value="entryForm.scene[f.key]" :placeholder="f.key.includes('date') ? 'YYYY-MM-DD' : ''" />
          </a-form-item>
        </template>
        <a-alert
          v-if="travelSubtype === 'allowance' && entryForm.entry_type === 'travel'"
          type="success"
          show-icon
          style="margin-bottom: 12px"
          :message="allowancePreview
            ? `补助金额 = ${entryForm.scene.days || 0} 天 × ${entryForm.scene.daily_standard || allowanceStandard} 元/天 = ${allowancePreview} 元`
            : `请填写补助天数；日标准留空按公司标准 ${allowanceStandard} 元/天`"
        >
          <template #description>
            补助无发票，将自动生成一条内部凭证（可税前扣除）；日标准默认取公司配置，可按实际调整。
          </template>
        </a-alert>
        <a-form-item label="备注">
          <a-input v-model:value="entryForm.note" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 发票池 -->
    <a-modal
      v-model:open="poolOpen"
      :title="`选取发票加入事项（本人上传的 + 公共池；已验真、未占用）`"
      width="820px"
      :confirm-loading="poolLoading"
      @ok="onAddSelected"
    >
      <a-space style="margin-bottom: 12px">
        <span>费用类型</span>
        <a-select v-model:value="poolExpenseType" style="width: 140px">
          <a-select-option v-for="(label, k) in EXPENSE_TYPE_LABELS" :key="k" :value="k">{{ label }}</a-select-option>
        </a-select>
      </a-space>
      <a-table
        :columns="[
          { title: '发票号码', dataIndex: 'invoice_number', key: 'invoice_number' },
          { title: '销售方', dataIndex: 'seller_name', key: 'seller_name' },
          { title: '开票日期', dataIndex: 'issue_date', key: 'issue_date', width: 120 },
          { title: '金额', dataIndex: 'total_amount', key: 'total_amount', width: 100 },
        ]"
        :data-source="pool"
        :loading="poolLoading"
        row-key="id"
        size="small"
        :pagination="{ pageSize: 10 }"
        :row-selection="{ selectedRowKeys: poolSelection, onChange: (k: number[]) => (poolSelection = k) }"
      />
      <p v-if="!poolLoading && pool.length === 0" style="color: #888">
        暂无可用发票：需已验真通过、未被拦截且未被其他报销单占用。
      </p>
    </a-modal>
  </div>
</template>
