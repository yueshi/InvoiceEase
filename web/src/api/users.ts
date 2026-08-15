// 用户管理 API
import { api } from "./client";
import type { UserCreate, UserOut, UserUpdate } from "../types";

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
