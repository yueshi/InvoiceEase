<!-- 原件预览 Modal：PDF 内联 iframe / XML 文本 / OFD 渲染图（后端 /preview 端点） -->
<script setup lang="ts">
import { ref, watch } from "vue";
import { errorMessage } from "../api/client";
import { previewInvoiceFile, type PreviewPayload } from "../api/invoices";
import type { InvoiceOut } from "../types";

const props = defineProps<{ open: boolean; invoice: InvoiceOut | null }>();
const loading = ref(false);
const payload = ref<PreviewPayload | null>(null);
const xmlText = ref("");

watch(
  () => [props.open, props.invoice] as const,
  async ([open, invoice]) => {
    payload.value = null;
    xmlText.value = "";
    if (!open || !invoice) return;
    loading.value = true;
    try {
      const p = await previewInvoiceFile(invoice.id, invoice.file_type, !!invoice.xml_url);
      if (p.kind === "xml") {
        const resp = await fetch(p.url);
        xmlText.value = await resp.text();
        URL.revokeObjectURL(p.url);
      } else {
        payload.value = p;
      }
    } catch (e) {
      errorMessage(e, "预览加载失败");
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <a-modal :open="open" :footer="null" width="860px" title="原件预览" @cancel="$emit('update:open', false)">
    <a-spin :spinning="loading">
      <div v-if="payload?.kind === 'pdf'">
        <iframe :src="payload.url" class="preview-frame" />
      </div>
      <div v-else-if="payload?.kind === 'ofd-image'">
        <img :src="payload.url" class="preview-img" />
      </div>
      <pre v-else-if="xmlText" class="preview-xml">{{ xmlText }}</pre>
      <div v-else-if="!loading" class="empty">无可预览内容</div>
    </a-spin>
  </a-modal>
</template>

<style scoped>
.preview-frame { width: 100%; height: 70vh; border: 1px solid #eee; }
.preview-img { max-width: 100%; }
.preview-xml { max-height: 70vh; overflow: auto; white-space: pre-wrap; }
.empty { color: var(--c-sub); }
</style>
