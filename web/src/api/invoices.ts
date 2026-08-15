import { api, downloadFile } from "./client";
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

export async function downloadInvoiceFile(id: number, kind: "file" | "xml"): Promise<void> {
  await downloadFile(`/invoices/${id}/file?kind=${kind}`, `invoice-${id}.${kind === "xml" ? "xml" : "bin"}`);
}
