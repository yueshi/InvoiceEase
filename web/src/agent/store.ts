// web/src/agent/store.ts
// Agent 会话 store：消息列表 + 流式状态机（token 合并 / tool_call start→done|failed）。
import { defineStore } from "pinia";
import * as api from "./api";
import type { AgentMessage, AgentSession, AgentToolCall, ChatContext, SSEEvent } from "./types";

export const useAgentStore = defineStore("agent", {
  state: () => ({
    drawerOpen: false,
    sessions: [] as AgentSession[],
    currentSessionId: null as number | null,
    messages: [] as AgentMessage[],
    streamingText: "",
    streamingTools: [] as AgentToolCall[],
    streaming: false,
    error: null as { code: string; message: string } | null,
    abort: null as AbortController | null,
  }),
  actions: {
    openDrawer() {
      this.drawerOpen = true;
      void this.ensureSession();
    },
    closeDrawer() {
      this.drawerOpen = false;
    },
    /** 登出/切换身份时清空（App.vue 调用）。 */
    reset() {
      this.cancel();
      this.drawerOpen = false;
      this.sessions = [];
      this.currentSessionId = null;
      this.messages = [];
      this.streamingText = "";
      this.streamingTools = [];
      this.error = null;
    },
    async loadSessions() {
      try {
        this.sessions = await api.listSessions();
      } catch {
        /* 抽屉打开失败不弹窗，静默（401 由 axios 拦截器统一跳登录） */
      }
    },
    async createSession() {
      const s = await api.createSession();
      this.sessions.unshift(s);
      this.currentSessionId = s.id;
      this.messages = [];
      this.error = null;
    },
    async switchSession(id: number) {
      this.currentSessionId = id;
      this.error = null;
      this.messages = await api.listMessages(id);
    },
    async removeSession(id: number) {
      await api.deleteSession(id);
      this.sessions = this.sessions.filter((s) => s.id !== id);
      if (this.currentSessionId === id) {
        this.currentSessionId = null;
        this.messages = [];
        await this.ensureSession();
      }
    },
    async ensureSession() {
      if (!this.sessions.length) await this.loadSessions();
      if (this.currentSessionId) return;
      if (this.sessions.length) await this.switchSession(this.sessions[0].id);
      else await this.createSession();
    },
    async sendMessage(text: string, context: ChatContext) {
      if (!this.currentSessionId || this.streaming) return;
      this.messages.push({
        id: -Date.now(), role: "user", content: text, created_at: "", tool_calls: null,
      });
      this.streamingText = "";
      this.streamingTools = [];
      this.streaming = true;
      this.error = null;
      const ac = new AbortController();
      this.abort = ac;

      const apply = (ev: SSEEvent) => {
        if (ev.type === "token") {
          this.streamingText += ev.data.text ?? "";
        } else if (ev.type === "tool_call") {
          const d = ev.data;
          if (d.status === "start") {
            this.streamingTools.push({ tool: d.tool ?? "?", status: "start" });
          } else {
            // 匹配最近一个还在 start 的同名工具（同一工具可能多轮调用）
            const pending = [...this.streamingTools]
              .reverse()
              .find((t) => t.tool === d.tool && t.status === "start");
            if (pending) {
              pending.status = d.status === "failed" ? "failed" : "done";
              pending.ms = d.ms;
            }
          }
        } else if (ev.type === "error") {
          this.error = { code: ev.data.code ?? "ERROR", message: ev.data.message ?? "出错了" };
        }
      };

      try {
        await api.streamMessage(this.currentSessionId, { message: text, context }, apply, ac.signal);
      } catch (e) {
        if (!ac.signal.aborted) {
          this.error = { code: "NETWORK", message: e instanceof Error ? e.message : "网络错误" };
        }
      } finally {
        if (this.streamingText || this.streamingTools.length) {
          this.messages.push({
            id: -Date.now() - 1,
            role: "assistant",
            content: this.streamingText,
            tool_calls: this.streamingTools.map((t) => ({ ...t })),
            created_at: "",
          });
        }
        this.streamingText = "";
        this.streamingTools = [];
        this.streaming = false;
        this.abort = null;
      }
    },
    cancel() {
      this.abort?.abort();
    },
  },
});
