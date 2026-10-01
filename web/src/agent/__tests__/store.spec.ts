// web/src/agent/__tests__/store.spec.ts
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import * as api from "../api";
import { useAgentStore } from "../store";
import type { SSEEvent } from "../types";

vi.mock("../api", () => ({
  listSessions: vi.fn().mockResolvedValue([]),
  createSession: vi.fn().mockResolvedValue({ id: 1, title: null, created_at: "", updated_at: "" }),
  deleteSession: vi.fn().mockResolvedValue(undefined),
  listMessages: vi.fn().mockResolvedValue([]),
  streamMessage: vi.fn(),
}));

describe("agent store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("token 合并 + tool_call 状态机（start→done 带耗时）", async () => {
    (api.streamMessage as Mock).mockImplementation(
      async (_sid: number, _body: unknown, onEvent: (e: SSEEvent) => void) => {
        onEvent({ type: "token", data: { text: "你" } });
        onEvent({ type: "token", data: { text: "好" } });
        onEvent({ type: "tool_call", data: { tool: "invoice_list", status: "start" } });
        onEvent({ type: "tool_call", data: { tool: "invoice_list", status: "done", ms: 12 } });
        onEvent({ type: "done", data: {} });
      },
    );
    const store = useAgentStore();
    store.currentSessionId = 1;
    await store.sendMessage("查发票", { page: "/invoices" });

    const last = store.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.content).toBe("你好");
    expect(last.tool_calls?.[0]).toMatchObject({ tool: "invoice_list", status: "done", ms: 12 });
    expect(store.streaming).toBe(false);
  });

  it("error 事件写入 error 状态", async () => {
    (api.streamMessage as Mock).mockImplementation(
      async (_sid: number, _body: unknown, onEvent: (e: SSEEvent) => void) => {
        onEvent({ type: "error", data: { code: "LLM_TIMEOUT", message: "模型超时" } });
      },
    );
    const store = useAgentStore();
    store.currentSessionId = 1;
    await store.sendMessage("hi", { page: "/" });
    expect(store.error).toEqual({ code: "LLM_TIMEOUT", message: "模型超时" });
  });

  it("cancel 中止流且不记为错误", async () => {
    (api.streamMessage as Mock).mockImplementation(
      (_sid: number, _body: unknown, _onEvent: unknown, signal: AbortSignal) =>
        new Promise((_res, rej) => {
          signal.addEventListener("abort", () => rej(new DOMException("Aborted", "AbortError")));
        }),
    );
    const store = useAgentStore();
    store.currentSessionId = 1;
    const p = store.sendMessage("hi", { page: "/" });
    store.cancel();
    await p;
    expect(store.streaming).toBe(false);
    expect(store.error).toBeNull();
  });

  it("流式中新建会话：旧流中止且不污染本地列表", async () => {
    (api.streamMessage as Mock).mockImplementation(
      (_sid: number, _body: unknown, onEvent: (e: SSEEvent) => void, signal: AbortSignal) =>
        new Promise((_res, rej) => {
          onEvent({ type: "token", data: { text: "旧会话回复" } });
          signal.addEventListener("abort", () => rej(new DOMException("Aborted", "AbortError")));
        }),
    );
    (api.createSession as Mock).mockResolvedValueOnce({ id: 2, title: null, created_at: "", updated_at: "" });
    const store = useAgentStore();
    store.currentSessionId = 1;
    const p = store.sendMessage("hi", { page: "/" });
    expect(store.streamingText).toBe("旧会话回复");

    await store.createSession();
    await p;

    expect(store.currentSessionId).toBe(2);
    expect(store.streaming).toBe(false);
    expect(store.streamingText).toBe("");
    expect(store.messages.some((m) => m.role === "assistant")).toBe(false);
    expect(store.messages.some((m) => m.content.includes("旧会话回复"))).toBe(false);
  });
});
