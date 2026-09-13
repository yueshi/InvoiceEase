// 常用企业银行账号 API（本司账户：回单「本司账户行」判定用）
import { api } from "./client";
import type { BankAccountCreate, BankAccountOut, BankAccountUpdate } from "../types";

export async function listBankAccounts(): Promise<BankAccountOut[]> {
  const { data } = await api.get<BankAccountOut[]>("/bank-accounts");
  return data;
}
export async function createBankAccount(body: BankAccountCreate): Promise<BankAccountOut> {
  const { data } = await api.post<BankAccountOut>("/bank-accounts", body);
  return data;
}
export async function updateBankAccount(id: number, body: BankAccountUpdate): Promise<BankAccountOut> {
  const { data } = await api.put<BankAccountOut>(`/bank-accounts/${id}`, body);
  return data;
}
export async function deleteBankAccount(id: number): Promise<void> {
  await api.delete(`/bank-accounts/${id}`);
}
