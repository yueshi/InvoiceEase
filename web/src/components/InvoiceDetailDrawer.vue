<!-- 发票详情抽屉：字段展示 + 下载原件/XML -->
<script setup lang="ts">
import { downloadInvoiceFile } from "../api/invoices";
import { INVOICE_STATUS_LABELS, VERIFY_STATUS_LABELS, type InvoiceOut } from "../types";

const props = defineProps<{ open: boolean; invoice: InvoiceOut | null }>();
const emit = defineEmits<{ "update:open": [boolean]; refresh: [] }>();

function onDownload(kind: "file" | "xml") {
  if (props.invoice) downloadInvoiceFile(props.invoice.id, kind);
}
</script>

<template>
  <a-drawer title="发票详情" :open="open" width="480" @close="emit('update:open', false)">
    <template v-if="invoice">
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
        </a-descriptions-item>
        <a-descriptions-item label="验真">{{ VERIFY_STATUS_LABELS[invoice.verify_status] || invoice.verify_status }}</a-descriptions-item>
        <a-descriptions-item label="解析来源">{{ invoice.parse_source || "—" }}（置信度 {{ invoice.confidence_score ?? "—" }}）</a-descriptions-item>
        <a-descriptions-item label="来源邮件">{{ invoice.email_subject || "—" }}</a-descriptions-item>
        <a-descriptions-item label="重复标记">{{ invoice.duplicate_flag ? "是" : "否" }}</a-descriptions-item>
      </a-descriptions>
      <a-space style="margin-top: 16px">
        <a-button @click="onDownload('file')">下载原件</a-button>
        <a-button @click="onDownload('xml')">下载 XML</a-button>
      </a-space>
    </template>
  </a-drawer>
</template>
