// MCP 访问令牌 API（自助签发 + 管理员代发/全局查看）
// 设计见 design/2026-09-13-MCP身份与权限设计.md §10
import { api } from "./client";
import type { McpTokenOut, McpTokenIssued } from "../types";

export interface IssueTokenBody {
  name: string;
  user_id?: number;
  scopes?: string[];
  expires_in_days?: number;
  never_expires?: boolean;
}

/** 令牌清单：默认只看自己的；管理员可 allUsers 查看全部（代管场景） */
export async function listMcpTokens(allUsers = false): Promise<McpTokenOut[]> {
  const { data } = await api.get<McpTokenOut[]>("/mcp-tokens", {
    params: allUsers ? { all_users: true } : {},
  });
  return data;
}

/** 签发令牌：响应里的 plaintext **仅此一次**，页面须立即展示并提供复制 */
export async function issueMcpToken(body: IssueTokenBody): Promise<McpTokenIssued> {
  const { data } = await api.post<McpTokenIssued>("/mcp-tokens", body);
  return data;
}

export async function revokeMcpToken(id: number): Promise<McpTokenOut> {
  const { data } = await api.post<McpTokenOut>(`/mcp-tokens/${id}/revoke`);
  return data;
}

/** 可用 scope 与各角色预设（渲染选择器 / 一键填充用） */
export async function fetchMcpScopes(): Promise<{
  scopes: string[];
  role_defaults: Record<string, string[]>;
}> {
  const { data } = await api.get<{ scopes: string[]; role_defaults: Record<string, string[]> }>(
    "/mcp-tokens/scopes",
  );
  return data;
}
