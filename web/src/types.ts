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
  validation_errors: Array<Record<string, unknown>> | null;
  verify_status: string;
  verify_detail: Record<string, unknown> | null;
  verify_is_mock: boolean;  // 验真结果为模拟 provider 产出（国税资质未获批前）
  verified_at: string | null;
  duplicate_flag: boolean;
  duplicate_of_id: number | null;
  reimbursement_status?: "none" | "pending" | "claimed";
  status: string;
  expense_type: string | null;
  cost_center: string | null;
  description: string | null;
  ai_review_verdict: string | null;
  ai_review_reason: string | null;
  ai_review_confidence: number | null;
  ai_reviewed_at: string | null;
  red_flag: boolean;  // 红字发票标记（M9 第一步：识别+标记，不自动对冲）
  xml_url: string | null;  // 合规存档：含数字签名的 XML 原件地址
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

export interface TrustStatsOut {
  days: number;
  auto_count: number;
  manual_count: number;
  overturn_count: number;
  overturn_rate: number;
}

export interface ReceiptOut {
  id: number;
  file_url: string;
  file_type: string;
  trade_date: string | null;
  counterparty_name: string | null;
  amount: string | null;
  abstract: string | null;
  direction: string | null;
  needs_review: boolean;
  quality_issues: string[] | null;
  bank_code: string | null;
  page_no: number | null;
  anchor: { bbox: [number, number, number, number] | null; text: string | null; v: number } | null;
  paired_invoice_id: number | null;
  status: string;
  created_at: string;
}

/** 回单上传批次（异步解析，R1.1） */
export interface ReceiptUploadOut {
  id: number;
  status: "parsing" | "parsed" | "failed";
  receipt_count: number;
  error: string | null;
  created_at: string;
  parsed_at: string | null;
}

export const INVOICE_STATUS_LABELS: Record<string, string> = {
  receiving: "收取中", received: "已收取", parsing: "解析中", parsed: "解析完成",
  verifying: "验真查重中", pending_submit: "待提交", pending_review: "待复核",
  blocked: "已拦截", rejected: "已驳回", submitted: "已提交", archived: "已归档",
};

export const VERIFY_STATUS_LABELS: Record<string, string> = {
  pending: "待验真", passed: "通过", failed: "失败",
};

export type AuditOutcome = "success" | "blocked" | "failed" | "error" | null;

export const AUDIT_OUTCOME_LABELS: Record<string, { text: string; color: string }> = {
  success: { text: "成功", color: "green" },
  blocked: { text: "已拦截", color: "orange" }, // 正常业务处置（重复/非发票），非失败
  failed: { text: "失败", color: "red" },
  error: { text: "异常", color: "red" },
};

export interface AuditOut {
  id: number; user_id: number | null; action: string; invoice_id: number | null;
  detail: Record<string, unknown> | null; outcome: AuditOutcome; ip_address: string | null;
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

export interface CompanyInfoOut {
  id: number;
  name: string;
  tax_id: string;
  kind: string;
  is_default: boolean;
  remark: string | null;
  bank_account: string | null;
  created_at: string;
  updated_at: string;
}
export interface CompanyInfoCreate {
  name: string;
  tax_id: string;
  kind?: string;
  is_default?: boolean;
  remark?: string | null;
  bank_account?: string | null;
}
export type CompanyInfoUpdate = Partial<CompanyInfoCreate>;
/** 常用企业银行账号（本司账户；回单「本司账户行」判定） */
export interface BankAccountOut {
  id: number;
  account_no: string;
  account_name: string | null;
  bank_name: string | null;
  bank_code: string | null;
  remark: string | null;
  is_default: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}
export interface BankAccountCreate {
  account_no: string;
  account_name?: string | null;
  bank_name?: string | null;
  bank_code?: string | null;
  remark?: string | null;
  is_default?: boolean;
  enabled?: boolean;
}
export type BankAccountUpdate = Partial<BankAccountCreate>;

// ---- 报销（P0）----------------------------------------------------------

export type ClaimStatus = "draft" | "pending_approval" | "approved" | "rejected" | "withdrawn";

export const CLAIM_STATUS_LABELS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  pending_approval: { text: "待审批", color: "processing" },
  approved: { text: "已通过", color: "green" },
  rejected: { text: "已驳回", color: "red" },
  withdrawn: { text: "已撤回", color: "default" },
};

export const VOUCHER_TYPE_LABELS: Record<string, string> = {
  invoice: "发票",
  bank_receipt: "银行回单",
  tax_receipt: "缴款书回单",
  receipt_voucher: "收款凭证（小额零星）",
  internal: "内部凭证（工资/补助）",
  contract: "合同/协议类",
  overseas: "境外票据",
};

export const EXPENSE_TYPE_LABELS: Record<string, string> = {
  travel: "差旅", office: "办公", entertainment: "招待", procurement: "采购", other: "其他",
};

export interface ClaimOut {
  id: number;
  claim_no: string;
  applicant_id: number;
  title: string;
  total_amount: string;
  status: ClaimStatus;
  approver_id: number | null;
  submitted_at: string | null;
  decided_at: string | null;
  rejected_reason: string | null;
  remark: string | null;
  created_at: string;
  item_count: number;
}

export interface ExpenseItemOut {
  id: number;
  claim_id: number;
  invoice_id: number | null;
  receipt_id: number | null;
  voucher_type: string;
  amount: string;
  expense_type: string;
  note: string | null;
  payee_name: string | null;
  payee_id_no: string | null;
  deductible: boolean;
  deductible_note: string | null;
  active: boolean;
}

export interface EntryOut {
  id: number;
  claim_id: number;
  entry_type: string;
  title: string;
  occurred_on: string | null;
  scene_fields: Record<string, string> | null;
  amount: string;
  note: string | null;
}

export interface ClaimDetailOut {
  claim: ClaimOut;
  entries: Array<EntryOut & { items: ExpenseItemOut[] }>;
  items: ExpenseItemOut[];
}

/** 事项类型 → 场景字段定义（动态表单 + 回显标签） */
export const SCENE_FIELDS: Record<string, Array<{ key: string; label: string; required?: boolean }>> = {
  travel: [
    { key: "from_city", label: "出发城市", required: true },
    { key: "to_city", label: "到达城市", required: true },
    { key: "start_date", label: "行程开始", required: true },
    { key: "end_date", label: "行程结束", required: true },
  ],
  procurement: [
    { key: "supplier", label: "供应商" },
    { key: "contract_no", label: "合同号" },
    { key: "order_no", label: "订单号" },
    { key: "acceptance_no", label: "验收单号" },
  ],
  entertainment: [
    { key: "guests", label: "招待对象", required: true },
    { key: "headcount", label: "招待人数", required: true },
  ],
  office: [],
  other: [],
};

export interface EligibleInvoiceOut {
  id: number;
  invoice_number: string | null;
  issue_date: string | null;
  seller_name: string | null;
  total_amount: string | null;
  expense_type: string | null;
}

export const BANK_LABELS: Record<string, string> = {
  ccb: "建设银行", icbc: "工商银行", abc: "农业银行", cmb: "招商银行", boc: "中国银行",
};

export const COMPANY_KIND_LABELS: Record<string, string> = {
  self: "本司", supplier: "供应商", other: "其他",
};
