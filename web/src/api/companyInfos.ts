// 常用税号及公司信息 API
import { api } from "./client";
import type { CompanyInfoCreate, CompanyInfoOut, CompanyInfoUpdate } from "../types";

export async function listCompanyInfos(): Promise<CompanyInfoOut[]> {
  const { data } = await api.get<CompanyInfoOut[]>("/company-infos");
  return data;
}
export async function createCompanyInfo(body: CompanyInfoCreate): Promise<CompanyInfoOut> {
  const { data } = await api.post<CompanyInfoOut>("/company-infos", body);
  return data;
}
export async function updateCompanyInfo(id: number, body: CompanyInfoUpdate): Promise<CompanyInfoOut> {
  const { data } = await api.put<CompanyInfoOut>(`/company-infos/${id}`, body);
  return data;
}
export async function deleteCompanyInfo(id: number): Promise<void> {
  await api.delete(`/company-infos/${id}`);
}
