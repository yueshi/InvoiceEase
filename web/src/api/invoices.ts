import { api, downloadFile, fetchBlobUrl } from "./client";
import type { InvoiceListResponse, InvoiceOut } from "../types";

export interface ListParams {
  status?: string;
  date_from?: string;
  date_to?: string;
  keyword?: string;
  /** 费用类型筛选；unclassified = 未归类（规则未命中留空） */
  expense_type?: string;
  /** 方向筛选：input 进项 / output 销项 */
  invoice_direction?: string;
  page?: number;
  page_size?: number;
}

export async function listInvoices(params: ListParams): Promise<InvoiceListResponse> {
  const { data } = await api.get<InvoiceListResponse>("/invoices", { params });
  return data;
}

export async function getInvoice(id: number): Promise<InvoiceOut> {
  const { data } = await api.get<InvoiceOut>(`/invoices/${id}`);
  return data;
}

export async function reviewInvoice(id: number, action: "approve" | "reject", note?: string): Promise<InvoiceOut> {
  const { data } = await api.post<InvoiceOut>(`/invoices/${id}/review`, { action, note });
  return data;
}

export async function reVerify(id: number): Promise<InvoiceOut> {
  const { data } = await api.post<InvoiceOut>(`/invoices/${id}/verify`);
  return data;
}

export async function updateInvoice(
  id: number,
  payload: { expense_type?: string | null; cost_center?: string | null; description?: string | null },
): Promise<InvoiceOut> {
  const { data } = await api.put<InvoiceOut>(`/invoices/${id}`, payload);
  return data;
}

/** 导入已开票（销项，文件解析）：XML/OFD/PDF，红票自动关联原蓝票 */
export async function importSalesInvoices(files: File[]): Promise<{ results: Array<Record<string, unknown>> }> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  const { data } = await api.post<{ results: Array<Record<string, unknown>> }>("/invoices/import-sales", form);
  return data;
}

/** 导入已开票（清单批量）：CSV/Excel（含「原发票号码」列则自动关联红票） */
export async function importSalesList(file: File): Promise<{ imported: number; skipped: number; errors: number }> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<{ imported: number; skipped: number; errors: number }>(
    "/invoices/import-sales-list", form,
  );
  return data;
}

/** 未关联原蓝票的红字票（财务待处理） */
export interface UnlinkedRedInvoice {
  id: number; invoice_number: string | null; buyer_name: string | null;
  total_amount: string | null; issue_date: string | null;
}
export async function listUnlinkedRed(): Promise<UnlinkedRedInvoice[]> {
  const { data } = await api.get<UnlinkedRedInvoice[]>("/invoices/red-unlinked");
  return data;
}

/** 人工补关联红票 → 原蓝票 */
export async function linkOriginalInvoice(redId: number, originalId: number): Promise<void> {
  await api.post(`/invoices/${redId}/link-original`, null, { params: { original_invoice_id: originalId } });
}

export async function uploadInvoice(file: File): Promise<InvoiceOut> {
  // 员工交票（M3）：仅 PDF/OFD/XML 原件；图片由后端 422 引导走邮箱
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<InvoiceOut>("/invoices/upload", form);
  return data;
}

export async function downloadInvoiceFile(id: number, kind: "file" | "xml"): Promise<void> {
  await downloadFile(`/invoices/${id}/file?kind=${kind}`, `invoice-${id}.${kind === "xml" ? "xml" : "bin"}`);
}

export interface PreviewPayload {
  url: string;      // blob objectURL
  kind: "pdf" | "xml" | "ofd-image";  // 前端渲染方式
  filename: string;
}

export async function previewInvoiceFile(id: number, fileType: string, hasXml = false): Promise<PreviewPayload> {
  // PDF/XML 走 /file 内联端点；OFD 走 /preview 渲染端点
  const path = fileType === "OFD" ? `/invoices/${id}/preview` : `/invoices/${id}/file?kind=${fileType === "XML" && hasXml ? "xml" : "file"}`;
  const url = await fetchBlobUrl(path);
  return {
    url,
    kind: fileType === "OFD" ? "ofd-image" : fileType === "XML" ? "xml" : "pdf",
    filename: `invoice-${id}`,
  };
}
