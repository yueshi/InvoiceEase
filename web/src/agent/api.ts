// web/src/agent/api.ts
// Agent HTTP 客户端。SSE 用 fetch + ReadableStream（axios 浏览器端不支持流式响应）。
import { api, TOKEN_KEY } from "../api/client";
import type { AgentMessage, AgentSession, ChatContext, SSEEvent } from "./types";

export async function listSessions(): Promise<AgentSession[]> {
  return (await api.get<AgentSession[]>("/agent/sessions")).data;
}

export async function createSession(): Promise<AgentSession> {
  return (await api.post<AgentSession>("/agent/sessions")).data;
}

export async function deleteSession(id: number): Promise<void> {
  await api.delete(`/agent/sessions/${id}`);
}

export async function listMessages(id: number): Promise<AgentMessage[]> {
  return (await api.get<AgentMessage[]>(`/agent/sessions/${id}/messages`)).data;
}

/** POST 一条消息并逐帧消费 SSE；signal.abort() 可中途取消。 */
export async function streamMessage(
  sessionId: number,
  body: { message: string; context: ChatContext },
  onEvent: (ev: SSEEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY);
  const resp = await fetch(`/api/v1/agent/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败（HTTP ${resp.status}）`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      try {
        onEvent(JSON.parse(dataLine.slice(5).trim()) as SSEEvent);
      } catch {
        // 空帧/非 JSON 帧跳过
      }
    }
  }
}
