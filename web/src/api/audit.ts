// 审计日志 API
import { api } from "./client";
import type { AuditListResponse } from "../types";

/** category：business=业务审计（默认，不含身份事件）/ security=安全审计（登录成败/登出）/ all */
export type AuditCategory = "business" | "security" | "all";

export interface AuditParams { user_id?: number; action?: string; category?: AuditCategory; outcome?: string; invoice_id?: number; date_from?: string; date_to?: string; page?: number; page_size?: number; }

export async function listAuditLogs(params: AuditParams): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>("/audit-logs", { params });
  return data;
}
