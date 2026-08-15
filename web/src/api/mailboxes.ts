// 邮箱配置 API
import { api } from "./client";
import type { MailboxCreate, MailboxOut, MailboxUpdate, PollResultOut } from "../types";

export async function listMailboxes(): Promise<MailboxOut[]> {
  const { data } = await api.get<MailboxOut[]>("/mailboxes");
  return data;
}
export async function createMailbox(body: MailboxCreate): Promise<MailboxOut> {
  const { data } = await api.post<MailboxOut>("/mailboxes", body);
  return data;
}
export async function updateMailbox(id: number, body: MailboxUpdate): Promise<MailboxOut> {
  const { data } = await api.put<MailboxOut>(`/mailboxes/${id}`, body);
  return data;
}
export async function testMailbox(id: number): Promise<{ ok: boolean; message: string }> {
  const { data } = await api.post<{ ok: boolean; message: string }>(`/mailboxes/${id}/test`);
  return data;
}
export async function pollMailbox(id: number): Promise<PollResultOut> {
  const { data } = await api.post<PollResultOut>(`/mailboxes/${id}/poll`);
  return data;
}
