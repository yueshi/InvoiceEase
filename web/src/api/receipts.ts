import { api, downloadFile, fetchBlobUrl } from "./client";
import type { ReceiptOut, ReceiptUploadOut } from "../types";

/** 查询周期：按年（YYYY）/ 按季度（YYYY-QN）/ 按月（YYYY-MM）；都不给 = 全部时间 */
export interface ReceiptPeriod {
  month?: string;
  quarter?: string;
  year?: string;
}

export async function listReceipts(period: ReceiptPeriod, unmatchedOnly = false): Promise<ReceiptOut[]> {
  const path = unmatchedOnly ? "/receipts/unmatched" : "/receipts";
  const { data } = await api.get<ReceiptOut[]>(path, { params: period });
  return data;
}

export async function uploadReceipt(file: File): Promise<{ upload_id: number; status: string }> {
  const form = new FormData();
  form.append("file", file);
  // 异步解析（R1.1）：立即返回批次号，解析在后台进行
  const { data } = await api.post<{ upload_id: number; status: string }>("/receipts/upload", form);
  return data;
}

export async function listReceiptUploads(): Promise<ReceiptUploadOut[]> {
  const { data } = await api.get<ReceiptUploadOut[]>("/receipts/uploads");
  return data;
}

/** 回单原件 blob URL（带鉴权；新标签页打开供人工核对/补录） */
export async function fetchReceiptFileUrl(receiptId: number): Promise<string> {
  return fetchBlobUrl(`/receipts/${receiptId}/file`);
}

/** 回单所在页渲染图 blob URL（原件定位高亮用；渲染不可用时后端 501） */
export async function fetchReceiptPageUrl(receiptId: number): Promise<string> {
  return fetchBlobUrl(`/receipts/${receiptId}/page.png?dpi=150`);
}

export async function autoPairReceipt(receiptId: number): Promise<ReceiptOut> {
  const { data } = await api.post<ReceiptOut>(`/receipts/${receiptId}/auto-pair`);
  return data;
}

export async function exportReceipts(period: ReceiptPeriod): Promise<void> {
  const key = period.quarter ?? period.month ?? period.year ?? "all";
  const query = period.quarter
    ? `quarter=${period.quarter}`
    : period.year
      ? `year=${period.year}`
      : period.month
        ? `month=${period.month}`
        : "";
  await downloadFile(`/receipts/export${query ? `?${query}` : ""}`, `receipts-${key}.csv`);
}
