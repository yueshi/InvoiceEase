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

  it("reasoning 事件累积进 streamingReasoning，归档对象带 reasoning 且 finally 复位", async () => {
    const seen: string[] = [];
    const store = useAgentStore();
    (api.streamMessage as Mock).mockImplementation(
      async (_sid: number, _body: unknown, onEvent: (e: SSEEvent) => void) => {
        onEvent({ type: "reasoning", data: { text: "先看" } });
        onEvent({ type: "reasoning", data: { text: "再看" } });
        seen.push(store.streamingReasoning);
        onEvent({ type: "token", data: { text: "答案" } });
      },
    );
    store.currentSessionId = 1;
    await store.sendMessage("查发票", { page: "/invoices" });

    expect(seen).toEqual(["先看再看"]); // 流式中实时累积
    const last = store.messages.at(-1)!;
    expect(last.content).toBe("答案");
    expect(last.reasoning).toBe("先看再看"); // 归档进本地消息（仅展示，不落库）
    expect(store.streamingReasoning).toBe(""); // finally 复位
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

  describe("面板宽度", () => {
    beforeEach(() => {
      localStorage.clear(); // 宽度偏好落 localStorage，需用例间隔离
    });

    it("下界：setDrawerWidth(100) → 320", () => {
      const store = useAgentStore();
      store.setDrawerWidth(100);
      expect(store.drawerWidth).toBe(320);
    });

    it("上界：setDrawerWidth(9999) → round(min(720, innerWidth*0.6))（jsdom 1024 → 614）", () => {
      const store = useAgentStore();
      store.setDrawerWidth(9999);
      expect(store.drawerWidth).toBe(Math.round(Math.min(720, window.innerWidth * 0.6)));
    });

    it("区间内原样：setDrawerWidth(500) → 500", () => {
      const store = useAgentStore();
      store.setDrawerWidth(500);
      expect(store.drawerWidth).toBe(500);
    });

    it("调宽写入 localStorage；无存储时初值 440", () => {
      const store = useAgentStore();
      store.setDrawerWidth(500);
      expect(localStorage.getItem("invoicing_agent_width")).toBe(String(store.drawerWidth));

      localStorage.clear();
      setActivePinia(createPinia()); // 新 store 实例才会重新读 localStorage
      expect(useAgentStore().drawerWidth).toBe(440);
    });
  });
});
