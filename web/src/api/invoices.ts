import { api, downloadFile, fetchBlobUrl } from "./client";
import type { InvoiceListResponse, InvoiceOut } from "../types";

export interface ListParams {
  status?: string;
  date_from?: string;
  date_to?: string;
  keyword?: string;
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
