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

/** 助手消息时间线块：思考/工具/文本，按事件到达顺序排列（相邻同类已合并） */
export type AgentBlock =
  | { type: "reasoning"; text: string }
  | { type: "text"; text: string }
  | { type: "tool"; tool: string; status: "start" | "done" | "failed"; ms?: number | null };

export interface AgentMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  tool_calls: AgentToolCall[] | null;
  /** 时间线（旧数据/旧本地消息无此字段，走兼容布局） */
  blocks?: AgentBlock[] | null;
  /** 思考过程：旧本地消息的兼容字段（新数据统一进 blocks） */
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
