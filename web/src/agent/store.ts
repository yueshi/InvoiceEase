// web/src/agent/store.ts
// Agent 会话 store：消息列表 + 流式状态机（token 合并 / tool_call start→done|failed）。
import { defineStore } from "pinia";
import * as api from "./api";
import type { AgentBlock, AgentMessage, AgentSession, AgentToolCall, ChatContext, SSEEvent } from "./types";

/** 时间线追加：reasoning/text 相邻同类合并文本，其余（工具块/换类）直接入列。 */
export function appendStreamBlock(blocks: AgentBlock[], block: AgentBlock): void {
  const tail = blocks[blocks.length - 1];
  if (block.type === "reasoning" && tail?.type === "reasoning") tail.text += block.text;
  else if (block.type === "text" && tail?.type === "text") tail.text += block.text;
  else blocks.push(block);
}

// ==== 面板宽度（用户偏好，localStorage 持久化；跨登出保留，reset() 不清） ====
const AGENT_WIDTH_KEY = "invoicing_agent_width";
const AGENT_WIDTH_DEFAULT = 440;
const AGENT_WIDTH_MIN = 320;
const AGENT_WIDTH_MAX = 720;

/** 动态 clamp：上限 = min(720, 窗口宽 60%)，窗口极窄时保底 320 */
function clampWidth(w: number): number {
  const upper = Math.round(Math.min(AGENT_WIDTH_MAX, window.innerWidth * 0.6));
  return Math.round(Math.min(Math.max(w, AGENT_WIDTH_MIN), Math.max(AGENT_WIDTH_MIN, upper)));
}

/**
 * 初始读取：只做 Number.isFinite 校验 + 静态上下限。
 * 不走 clampWidth——那会让 innerWidth 变小时静默改写用户存的偏好。
 */
function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(AGENT_WIDTH_KEY);
    if (raw === null) return AGENT_WIDTH_DEFAULT;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return AGENT_WIDTH_DEFAULT;
    return Math.min(Math.max(parsed, AGENT_WIDTH_MIN), AGENT_WIDTH_MAX);
  } catch {
    return AGENT_WIDTH_DEFAULT; // localStorage 不可用（隐私模式等）时用默认
  }
}

export const useAgentStore = defineStore("agent", {
  state: () => ({
    drawerOpen: false,
    drawerWidth: readStoredWidth(),
    sessions: [] as AgentSession[],
    currentSessionId: null as number | null,
    messages: [] as AgentMessage[],
    /** 流式时间线：思考/工具/文本按事件到达顺序（相邻同类合并） */
    streamingBlocks: [] as AgentBlock[],
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
    /** 拖拽调宽 / 双击复位：clamp 后写入 localStorage */
    setDrawerWidth(w: number) {
      this.drawerWidth = clampWidth(w);
      try {
        localStorage.setItem(AGENT_WIDTH_KEY, String(this.drawerWidth));
      } catch {
        /* 写入失败（隐私模式等）：本次会话内宽度仍生效 */
      }
    },
    /** 登出/切换身份时清空（App.vue 调用）。 */
    reset() {
      this.cancel();
      this.drawerOpen = false;
      this.sessions = [];
      this.currentSessionId = null;
      this.messages = [];
      this.streamingBlocks = [];
      this.error = null;
      this.streaming = false;
      this.abort = null;
    },
    async loadSessions() {
      try {
        this.sessions = await api.listSessions();
      } catch {
        /* 抽屉打开失败不弹窗，静默（401 由 axios 拦截器统一跳登录） */
      }
    },
    async createSession() {
      this.cancel(); // 裁决(a) 同源：新建即切走当前会话，先中止在途流，避免旧回复渲染进新会话视图
      const s = await api.createSession();
      this.sessions.unshift(s);
      this.currentSessionId = s.id;
      this.messages = [];
      this.error = null;
    },
    async switchSession(id: number) {
      this.cancel(); // 裁决(a)：切换会话先中止在途流，避免回复落入错误会话的本地列表
      this.currentSessionId = id;
      this.error = null;
      this.messages = await api.listMessages(id);
    },
    async removeSession(id: number) {
      this.cancel(); // 裁决(a)：删除会话先中止在途流
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
      const sid = this.currentSessionId; // 裁决(b)：流式期间可能切换会话，归档时需比对
      this.messages.push({
        id: -Date.now(), role: "user", content: text, created_at: "", tool_calls: null,
      });
      this.streamingBlocks = [];
      this.streaming = true;
      this.error = null;
      const ac = new AbortController();
      this.abort = ac;

      const apply = (ev: SSEEvent) => {
        if (ev.type === "token") {
          appendStreamBlock(this.streamingBlocks, { type: "text", text: ev.data.text ?? "" });
        } else if (ev.type === "reasoning") {
          appendStreamBlock(this.streamingBlocks, { type: "reasoning", text: ev.data.text ?? "" });
        } else if (ev.type === "tool_call") {
          const d = ev.data;
          if (d.status === "start") {
            this.streamingBlocks.push({ type: "tool", tool: d.tool ?? "?", status: "start", ms: null });
          } else {
            // 匹配最近一个还在 start 的同名工具（同一工具可能多轮调用）
            const pending = [...this.streamingBlocks]
              .reverse()
              .find((b) => b.type === "tool" && b.tool === d.tool && b.status === "start");
            if (pending && pending.type === "tool") {
              pending.status = d.status === "failed" ? "failed" : "done";
              pending.ms = d.ms ?? null;
            }
          }
        } else if (ev.type === "error") {
          this.error = { code: ev.data.code ?? "ERROR", message: ev.data.message ?? "出错了" };
        }
      };

      try {
        await api.streamMessage(sid, { message: text, context }, apply, ac.signal);
      } catch (e) {
        if (!ac.signal.aborted) {
          // fetch 网络层失败抛 TypeError（"Failed to fetch"）→ 给可操作提示，与 axios 侧文案一致
          const msg =
            e instanceof TypeError
              ? "无法连接服务器，请确认后端服务已启动"
              : e instanceof Error
                ? e.message
                : "网络错误";
          this.error = { code: "NETWORK", message: msg };
        }
      } finally {
        // 会话已切走时不再归档（后端已持久化，切回时 loadMessages 会拉回）
        if (this.currentSessionId === sid && this.streamingBlocks.length) {
          // content/tool_calls 为旧字段，供旧渲染路径与兼容逻辑消费；blocks 是新的时间线
          let content = "";
          const toolCalls: AgentToolCall[] = [];
          for (const b of this.streamingBlocks) {
            if (b.type === "text") content += b.text;
            else if (b.type === "tool") toolCalls.push({ tool: b.tool, status: b.status, ms: b.ms ?? undefined });
          }
          this.messages.push({
            id: -Date.now() - 1,
            role: "assistant",
            content,
            tool_calls: toolCalls,
            blocks: this.streamingBlocks.map((b) => ({ ...b })),
            created_at: "",
          });
        }
        this.streamingBlocks = [];
        this.streaming = false;
        this.abort = null;
      }
    },
    cancel() {
      this.abort?.abort();
    },
  },
});
