<!-- 回单详情抽屉：全字段 + 质量问题 + 原件操作（列表列精简，细节收进此处） -->
<script setup lang="ts">
import dayjs from "dayjs";
import { computed } from "vue";
import { message } from "ant-design-vue";
import { setReceiptCategory } from "../api/receipts";
import { BANK_LABELS, RECEIPT_CATEGORY_LABELS, type ReceiptOut } from "../types";
import { receiptStatusColor, receiptStatusText } from "../utils/format";

const props = defineProps<{ open: boolean; receipt: ReceiptOut | null }>();
const emit = defineEmits<{
  "update:open": [boolean];
  viewFile: [ReceiptOut];
  viewPage: [ReceiptOut];
  locate: [ReceiptOut];
  refresh: [];
}>();

/** 交易性质人工覆盖（"auto" 由后端还原规则判定）；成功后请列表刷新以同步状态列 */
async function onCategoryChange(value: string) {
  if (!props.receipt) return;
  try {
    await setReceiptCategory(props.receipt.id, value);
    message.success("交易性质已更新");
    emit("refresh");
  } catch {
    message.error("交易性质更新失败");
  }
}

/** 选择器选项：9 个业务性质 + 「跟随规则」（后端 category="auto" 清除人工覆盖） */
const CATEGORY_OPTIONS = [
  ...Object.entries(RECEIPT_CATEGORY_LABELS).map(([value, label]) => ({ value, label })),
  { value: "auto", label: "跟随规则（清除人工覆盖）" },
];

/** 来源标记：未知值不静默当「规则」 */
const sourceLabel = computed(() => {
  const s = props.receipt?.category_source;
  return s === "manual" ? "（人工）" : s === "rule" ? "（规则）" : "—";
});

/** 发票要求提示：none/issue 各有说法（issue=客户回款，我方需开销项票），fetch 不提示 */
const requirementHint = computed(() => {
  const req = props.receipt?.invoice_requirement;
  if (req === "none") return "该性质无需发票：缴款书或银行凭证即凭证";
  if (req === "issue") return "客户回款：我方需开具销项发票（不进项抵扣）";
  return "";
});

const REVIEW_ISSUE_LABELS: Record<string, string> = {
  self_account_row: "本司账户行（对方已记为开户银行）",
  no_counterparty: "无对方户名（本司账户行，未识别到银行）",
  account_like_party: "户名疑为账户持有人",
  no_trade_date: "缺交易日期",
};

function issueText(r: ReceiptOut | null): string {
  const issues = r?.quality_issues || [];
  if (!issues.length) return "—";
  return issues.map((i) => REVIEW_ISSUE_LABELS[i] || i).join("；");
}
</script>

<template>
  <a-drawer title="回单详情" :open="props.open" width="480" @close="emit('update:open', false)">
    <template v-if="props.receipt">
      <a-descriptions :column="1" bordered size="small">
        <a-descriptions-item label="银行">
          {{ props.receipt.bank_code ? (BANK_LABELS[props.receipt.bank_code] || props.receipt.bank_code) : "未识别" }}
        </a-descriptions-item>
        <a-descriptions-item label="交易日期">
          {{ props.receipt.trade_date || "—" }}
        </a-descriptions-item>
        <a-descriptions-item label="收付方向">
          {{ props.receipt.direction || "—" }}
        </a-descriptions-item>
        <a-descriptions-item label="对方户名">
          {{ props.receipt.counterparty_name || "（无对方户名）" }}
        </a-descriptions-item>
        <a-descriptions-item label="金额">{{ props.receipt.amount || "—" }}</a-descriptions-item>
        <a-descriptions-item label="摘要">{{ props.receipt.abstract || "—" }}</a-descriptions-item>
        <a-descriptions-item label="状态">
          <a-tag :color="receiptStatusColor(props.receipt)">{{ receiptStatusText(props.receipt) }}</a-tag>
          <a-tag v-if="props.receipt.needs_review" color="red" class="gap-tag">待核对</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="交易性质">
          <a-select
            :value="props.receipt.category"
            size="small"
            style="width: 200px"
            :options="CATEGORY_OPTIONS"
            @change="onCategoryChange"
          />
          <span class="sub">{{ sourceLabel }}</span>
          <div v-if="requirementHint" class="sub">{{ requirementHint }}</div>
        </a-descriptions-item>
        <a-descriptions-item label="质量问题">
          <span :class="{ 'warn-strong': props.receipt.needs_review }">
            {{ issueText(props.receipt) }}
          </span>
        </a-descriptions-item>
        <a-descriptions-item label="发票配对">
          {{ props.receipt.paired_invoice_id ? `#${props.receipt.paired_invoice_id}` : "未配对" }}
        </a-descriptions-item>
        <a-descriptions-item label="原件位置">
          <template v-if="props.receipt.page_no">
            第 {{ props.receipt.page_no }} 页
            <span v-if="props.receipt.anchor?.bbox" class="sub">（可高亮定位）</span>
            <span v-else class="sub">（仅页码，无精确区域）</span>
          </template>
          <template v-else>未定位</template>
        </a-descriptions-item>
        <a-descriptions-item label="入库时间">
          {{ props.receipt.created_at ? dayjs(props.receipt.created_at).format("YYYY-MM-DD HH:mm:ss") : "—" }}
        </a-descriptions-item>
      </a-descriptions>

      <div class="wrap-row mt-4">
        <a-button type="primary" @click="emit('viewFile', props.receipt!)">查看原件</a-button>
        <a-button @click="emit('locate', props.receipt!)">定位高亮</a-button>
        <a-button @click="emit('viewPage', props.receipt!)">
          打开原 PDF{{ props.receipt.page_no ? `第 ${props.receipt.page_no} 页` : "" }}
        </a-button>
      </div>
    </template>
  </a-drawer>
</template>

<style scoped>
.gap-tag { margin-left: var(--space-1); }
.warn-strong { color: var(--c-danger); }
.sub { color: var(--c-sub); }
.mt-4 { margin-top: var(--space-4); }
</style>
