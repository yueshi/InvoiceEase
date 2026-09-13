// 报销 API（P0：建单/明细/提交/审批/撤回 + 可选发票池）
import { api } from "./client";
import type { ClaimDetailOut, ClaimOut, EligibleInvoiceOut, EntryOut, ExpenseItemOut } from "../types";

export async function listClaims(status?: string, claimType?: string): Promise<ClaimOut[]> {
  const { data } = await api.get<ClaimOut[]>("/expenses", {
    params: { status, claim_type: claimType },
  });
  return data;
}

export async function createClaim(title: string, remark?: string, claimType?: string): Promise<ClaimOut> {
  const { data } = await api.post<ClaimOut>("/expenses", { title, remark, claim_type: claimType });
  return data;
}

export async function getClaim(id: number): Promise<ClaimDetailOut> {
  const { data } = await api.get<ClaimDetailOut>(`/expenses/${id}`);
  return data;
}

export async function eligibleInvoices(): Promise<EligibleInvoiceOut[]> {
  const { data } = await api.get<EligibleInvoiceOut[]>("/expenses/eligible-invoices");
  return data;
}

export async function createEntry(
  claimId: number,
  body: { entry_type: string; title: string; occurred_on?: string | null; scene_fields?: Record<string, string> | null; note?: string },
): Promise<EntryOut> {
  const { data } = await api.post<EntryOut>(`/expenses/${claimId}/entries`, body);
  return data;
}

export async function removeEntry(claimId: number, entryId: number): Promise<void> {
  await api.delete(`/expenses/${claimId}/entries/${entryId}`);
}

export async function addInvoiceToClaim(
  claimId: number, entryId: number, invoiceId: number, expenseType = "other", note?: string,
): Promise<ExpenseItemOut> {
  const { data } = await api.post<ExpenseItemOut>(`/expenses/${claimId}/entries/${entryId}/invoices`, {
    invoice_id: invoiceId, expense_type: expenseType, note,
  });
  return data;
}

export async function addVoucherToClaim(
  claimId: number,
  entryId: number,
  body: {
    voucher_type: string; amount: string; expense_type?: string; note?: string;
    payee_name?: string | null; payee_id_no?: string | null;
  },
): Promise<ExpenseItemOut> {
  const { data } = await api.post<ExpenseItemOut>(
    `/expenses/${claimId}/entries/${entryId}/vouchers`, body,
  );
  return data;
}

export async function removeItem(itemId: number): Promise<void> {
  await api.delete(`/expenses/items/${itemId}`);
}

export async function submitClaim(id: number): Promise<ClaimOut> {
  const { data } = await api.post<ClaimOut>(`/expenses/${id}/submit`);
  return data;
}

export async function approveClaim(id: number): Promise<ClaimOut> {
  const { data } = await api.post<ClaimOut>(`/expenses/${id}/approve`);
  return data;
}

export async function rejectClaim(id: number, reason: string): Promise<ClaimOut> {
  const { data } = await api.post<ClaimOut>(`/expenses/${id}/reject`, { reason });
  return data;
}

export async function deleteClaim(id: number): Promise<void> {
  await api.delete(`/expenses/${id}`);
}

export async function withdrawClaim(id: number): Promise<ClaimOut> {
  const { data } = await api.post<ClaimOut>(`/expenses/${id}/withdraw`);
  return data;
}
