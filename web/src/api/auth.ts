// 认证相关 API：登录、登出、当前用户
import { api } from "./client";
import type { LoginResponse, UserOut } from "../types";

export async function login(username: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", { username, password });
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

export async function fetchMe(): Promise<UserOut> {
  const { data } = await api.get<UserOut>("/auth/me");
  return data;
}
