// web/src/agent/types.ts
export interface SSEEvent {
  type: "token" | "tool_call" | "reasoning" | "done" | "error";
  data: {
    text?: string;
    tool?: string;
    status?: "start" | "done" | "failed";
    ms?: number;
    code?: string;
    message?: string;
    [k: string]: unknown;
  };
}

export interface AgentSession {
  id: number;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentToolCall {
  tool: string;
  status: "start" | "done" | "failed";
  ms?: number;
}

export interface AgentMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  tool_calls: AgentToolCall[] | null;
  /** 思考过程：仅流式会话内本地展示，不落库（历史消息无此字段） */
  reasoning?: string | null;
  duration_ms?: number | null;
  created_at: string;
}

export interface ChatContext {
  page: string;
  invoice_id?: number | null;
  claim_id?: number | null;
  receipt_id?: number | null;
  month?: string | null;
}
