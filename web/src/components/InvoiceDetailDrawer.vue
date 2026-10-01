<!-- 发票详情抽屉：字段展示 + 下载原件/XML + 费用归类 -->
<script setup lang="ts">
import { message } from "ant-design-vue";
import { downloadInvoiceFile, updateInvoice } from "../api/invoices";
import { INVOICE_STATUS_LABELS, VERIFY_STATUS_LABELS, type InvoiceOut } from "../types";

const props = defineProps<{ open: boolean; invoice: InvoiceOut | null }>();
const emit = defineEmits<{ "update:open": [boolean]; refresh: [] }>();

function onDownload(kind: "file" | "xml") {
  if (props.invoice) downloadInvoiceFile(props.invoice.id, kind);
}

const EXPENSE_TYPE_OPTIONS = [
  { label: "差旅", value: "travel" },
  { label: "办公", value: "office" },
  { label: "招待", value: "entertainment" },
  { label: "采购", value: "procurement" },
  { label: "其他", value: "other" },
];

async function onClassify(value: string) {
  if (!props.invoice) return;
  try {
    await updateInvoice(props.invoice.id, { expense_type: value });
    message.success("已归类");
    emit("refresh");
  } catch {
    message.error("归类失败");
  }
}

async function onCostCenter(e: Event) {
  const value = (e.target as HTMLInputElement).value;
  if (!props.invoice) return;
  try {
    await updateInvoice(props.invoice.id, { cost_center: value || null });
    message.success("已保存");
    emit("refresh");
  } catch {
    message.error("保存失败");
  }
}
</script>

<template>
  <a-drawer title="发票详情" :open="open" width="480" @close="emit('update:open', false)">
    <template v-if="invoice">
      <a-alert v-if="invoice.ai_review_verdict" :type="invoice.ai_review_verdict === 'approve' ? 'success' : invoice.ai_review_verdict === 'reject' ? 'error' : 'warning'" class="mb-3">
        <template #message>
          AI 预判：
          <b>{{ { approve: '建议通过', reject: '建议拦截', uncertain: '存疑' }[invoice.ai_review_verdict as 'approve' | 'reject' | 'uncertain'] }}</b>
          <span v-if="invoice.ai_review_confidence != null">（置信度 {{ Math.round(invoice.ai_review_confidence * 100) }}%）</span>
          <div class="reason">{{ invoice.ai_review_reason }}</div>
        </template>
      </a-alert>
      <a-descriptions :column="1" size="small" bordered>
        <a-descriptions-item label="发票号码">{{ invoice.invoice_number || "—" }}</a-descriptions-item>
        <a-descriptions-item label="开票日期">{{ invoice.issue_date || "—" }}</a-descriptions-item>
        <a-descriptions-item label="不含税金额">{{ invoice.amount_without_tax ?? "—" }}</a-descriptions-item>
        <a-descriptions-item label="税额">{{ invoice.tax_amount ?? "—" }}</a-descriptions-item>
        <a-descriptions-item label="价税合计">{{ invoice.total_amount ?? "—" }}<template v-if="invoice.total_amount_cn">（{{ invoice.total_amount_cn }}）</template></a-descriptions-item>
        <a-descriptions-item label="销售方">{{ invoice.seller_name || "—" }}（{{ invoice.seller_tax_id || "—" }}）</a-descriptions-item>
        <a-descriptions-item label="购买方">{{ invoice.buyer_name || "—" }}（{{ invoice.buyer_tax_id || "—" }}）</a-descriptions-item>
        <a-descriptions-item label="状态">
          <a-tag>{{ INVOICE_STATUS_LABELS[invoice.status] || invoice.status }}</a-tag>
          <a-tag v-if="invoice.red_flag" color="red" class="gap-tag">红字发票</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="验真">
          {{ VERIFY_STATUS_LABELS[invoice.verify_status] || invoice.verify_status }}
          <a-tag v-if="invoice.verify_is_mock" color="orange">模拟模式</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="解析来源">{{ invoice.parse_source || "—" }}（置信度 {{ invoice.confidence_score ?? "—" }}）
          <a-tag v-if="invoice.confidence_score !== null && invoice.confidence_score < 0.8" color="orange">需人工核对</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="来源邮件">{{ invoice.email_subject || "—" }}</a-descriptions-item>
        <a-descriptions-item label="提交人">{{ invoice.submitted_by_name || "—" }}</a-descriptions-item>
        <a-descriptions-item label="重复标记">{{ invoice.duplicate_flag ? "是" : "否" }}</a-descriptions-item>
        <a-descriptions-item v-if="invoice.validation_errors && invoice.validation_errors.length" label="校验/解析问题">
          <ul class="err-list">
            <li v-for="(err, idx) in invoice.validation_errors" :key="idx">
              {{ err.code }}：{{ err.message }}
            </li>
          </ul>
        </a-descriptions-item>
      </a-descriptions>
      <a-divider>费用归类</a-divider>
      <a-space direction="vertical" style="width: 100%">
        <a-select
          :value="invoice.expense_type ?? undefined"
          placeholder="费用类型"
          style="width: 100%"
          :options="EXPENSE_TYPE_OPTIONS"
          @change="onClassify"
        />
        <a-input
          :value="invoice.cost_center ?? undefined"
          placeholder="部门/项目（可空）"
          @press-enter="onCostCenter"
        />
      </a-space>
      <a-space class="mt-4">
        <a-button @click="onDownload('file')">下载原件</a-button>
        <a-button @click="onDownload('xml')">下载 XML</a-button>
      </a-space>
    </template>
  </a-drawer>
</template>

<style scoped>
.reason { font-weight: normal; }
.err-list { margin: 0; padding-left: 16px; }
.gap-tag { margin-left: var(--space-1); }
.mt-4 { margin-top: var(--space-4); }
.mb-3 { margin-bottom: var(--space-3); }
</style>
