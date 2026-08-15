// 核心类型定义：角色、用户、登录响应
export type Role = "employee" | "finance_staff" | "finance_manager" | "admin";

export interface UserOut {
  id: number;
  username: string;
  role: Role;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
}

export const ROLE_LABELS: Record<Role, string> = {
  employee: "普通员工",
  finance_staff: "财务专员",
  finance_manager: "财务主管",
  admin: "系统管理员",
};

export interface InvoiceOut {
  id: number;
  tenant_id: string;
  user_id: number | null;
  mailbox_id: number | null;
  email_subject: string | null;
  invoice_code: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  amount_without_tax: string | null;
  tax_amount: string | null;
  total_amount: string | null;
  total_amount_cn: string | null;
  seller_name: string | null;
  seller_tax_id: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  invoice_type: string | null;
  file_type: string;
  parse_source: string | null;
  confidence_score: number | null;
  validation_errors: Record<string, unknown> | null;
  verify_status: string;
  verify_detail: Record<string, unknown> | null;
  verified_at: string | null;
  duplicate_flag: boolean;
  duplicate_of_id: number | null;
  status: string;
  review_note: string | null;
  reviewed_by: number | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvoiceListResponse {
  items: InvoiceOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface StatsOverviewOut {
  pending_review: number;
  pending_submit: number;
  today_new: number;
  month_total: number;
}

export const INVOICE_STATUS_LABELS: Record<string, string> = {
  receiving: "收取中", received: "已收取", parsing: "解析中", parsed: "解析完成",
  verifying: "验真查重中", pending_submit: "待提交", pending_review: "待复核",
  blocked: "已拦截", rejected: "已驳回", submitted: "已提交", archived: "已归档",
};

export const VERIFY_STATUS_LABELS: Record<string, string> = {
  pending: "待验真", passed: "通过", failed: "失败",
};

export interface AuditOut {
  id: number; user_id: number | null; action: string; invoice_id: number | null;
  detail: Record<string, unknown> | null; ip_address: string | null;
  channel: string; created_at: string;
}
export interface AuditListResponse { items: AuditOut[]; total: number; page: number; page_size: number; }

export interface MailboxOut {
  id: number; name: string; mailbox_type: string; imap_host: string; imap_port: number; use_ssl: boolean;
  username: string; folder: string; keywords: string; poll_interval_seconds: number;
  smtp_host: string | null; smtp_port: number | null; smtp_username: string | null;
  agently_workspace: string | null;
  enabled: boolean; last_polled_at: string | null; last_uid: number;
  created_at: string; updated_at: string;
}
export interface MailboxCreate { name: string; mailbox_type?: string; imap_host: string; imap_port?: number; use_ssl?: boolean; username: string; password: string; folder?: string; keywords?: string; poll_interval_seconds?: number; smtp_host?: string | null; smtp_port?: number | null; smtp_username?: string | null; smtp_password?: string | null; agently_workspace?: string | null; agently_token?: string | null; }
export type MailboxUpdate = Partial<MailboxCreate>;
export interface PollResultOut { received: number; rejected_images: number; ignored: number; duplicates: number; errors: number; }

export interface UserCreate { username: string; password: string; role: Role; }
export interface UserUpdate { password?: string | null; role?: Role; }
