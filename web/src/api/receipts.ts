import { api, downloadFile } from "./client";
import type { ReceiptOut } from "../types";

export async function listReceipts(month: string, unmatchedOnly = false): Promise<ReceiptOut[]> {
  const path = unmatchedOnly ? "/receipts/unmatched" : "/receipts";
  const { data } = await api.get<ReceiptOut[]>(path, { params: { month } });
  return data;
}

export async function uploadReceipt(file: File): Promise<ReceiptOut[]> {
  const form = new FormData();
  form.append("file", file);
  // 一份 PDF 可含多张回单：后端逐张入库，返回数组
  const { data } = await api.post<ReceiptOut[]>("/receipts/upload", form);
  return data;
}

export async function autoPairReceipt(receiptId: number): Promise<ReceiptOut> {
  const { data } = await api.post<ReceiptOut>(`/receipts/${receiptId}/auto-pair`);
  return data;
}

export async function exportReceipts(month: string): Promise<void> {
  await downloadFile(`/receipts/export?month=${month}`, `receipts-${month}.csv`);
}
