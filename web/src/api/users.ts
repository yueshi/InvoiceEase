// 用户管理 API
import { api } from "./client";
import type { ResetPasswordOut, UserCreate, UserOut, UserUpdate } from "../types";

export async function listUsers(): Promise<UserOut[]> {
  const { data } = await api.get<UserOut[]>("/users");
  return data;
}
export async function createUser(body: UserCreate): Promise<UserOut> {
  const { data } = await api.post<UserOut>("/users", body);
  return data;
}
export async function updateUser(id: number, body: UserUpdate): Promise<UserOut> {
  const { data } = await api.put<UserOut>(`/users/${id}`, body);
  return data;
}

/** 管理员重置他人密码：generate=true 自动生成（明文只回显一次） */
export async function resetUserPassword(
  id: number,
  body: { new_password?: string; generate?: boolean },
): Promise<ResetPasswordOut> {
  const { data } = await api.post<ResetPasswordOut>(`/users/${id}/password`, body);
  return data;
}

/** 暂停：登录、已签发 JWT、MCP 令牌同时立即失效 */
export async function suspendUser(id: number): Promise<UserOut> {
  const { data } = await api.post<UserOut>(`/users/${id}/suspend`);
  return data;
}

export async function resumeUser(id: number): Promise<UserOut> {
  const { data } = await api.post<UserOut>(`/users/${id}/resume`);
  return data;
}
