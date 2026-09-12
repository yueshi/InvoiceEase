import { api, downloadFile } from "./client";
import type { ReceiptOut, ReceiptUploadOut } from "../types";

export async function listReceipts(month: string, unmatchedOnly = false): Promise<ReceiptOut[]> {
  const path = unmatchedOnly ? "/receipts/unmatched" : "/receipts";
  const { data } = await api.get<ReceiptOut[]>(path, { params: { month } });
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

export async function autoPairReceipt(receiptId: number): Promise<ReceiptOut> {
  const { data } = await api.post<ReceiptOut>(`/receipts/${receiptId}/auto-pair`);
  return data;
}

export async function exportReceipts(month: string): Promise<void> {
  await downloadFile(`/receipts/export?month=${month}`, `receipts-${month}.csv`);
}
