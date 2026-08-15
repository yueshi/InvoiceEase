// 审计日志 API
import { api } from "./client";
import type { AuditListResponse } from "../types";

export interface AuditParams { user_id?: number; action?: string; invoice_id?: number; date_from?: string; date_to?: string; page?: number; page_size?: number; }

export async function listAuditLogs(params: AuditParams): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>("/audit-logs", { params });
  return data;
}
