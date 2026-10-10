// 全屏对话式报销页测试（P2 §6.2：报销发起对话）
// 关键契约：复用同一 agent store（抽屉与全屏页共享会话），入口卡只预填不代发
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useAgentStore } from "../../agent/store";
import ChatExpenseView from "../ChatExpenseView.vue";

vi.mock("vue-router", () => ({ useRoute: () => ({ path: "/expenses/chat", query: {} }) }));
vi.mock("../../agent/api", () => ({
  listSessions: vi.fn().mockResolvedValue([]),
  createSession: vi.fn().mockResolvedValue({ id: 1, title: "新会话", created_at: "" }),
  deleteSession: vi.fn(),
  listMessages: vi.fn().mockResolvedValue([]),
  streamMessage: vi.fn(),
}));

beforeAll(() => {
  // jsdom 无 Element.prototype.scrollTo；MessageList 的滚动 watcher 会调用
  (Element.prototype as unknown as { scrollTo: () => void }).scrollTo = () => {};
});

describe("ChatExpenseView", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
  });

  it("渲染全屏页标题与 5 个领域入口", () => {
    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    expect(w.text()).toContain("报销助手");
    expect(w.findAll("button.ec-item")).toHaveLength(5);
  });

  it("点入口卡 → 预填输入框且不发送", async () => {
    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    await w.findAll("button.ec-item")[0].trigger("click");
    await w.vm.$nextTick();
    const ta = w.find("textarea").element as HTMLTextAreaElement;
    expect(ta.value).toBe("我要报出差费用");
  });

  it("复用同一 agent store：抽屉里已有的消息在全屏页可见", async () => {
    const store = useAgentStore();
    store.messages = [
      { id: 1, role: "assistant", content: "已有会话消息", tool_calls: null,
        created_at: "", blocks: [{ type: "text", text: "已有会话消息" }] },
    ] as never;
    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    expect(w.text()).toContain("已有会话消息");
  });

  it("有消息时不再显示入口卡（避免刷屏）", async () => {
    const store = useAgentStore();
    store.messages = [
      { id: 1, role: "user", content: "hi", tool_calls: null, created_at: "" },
    ] as never;
    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    expect(w.findAll("button.ec-item")).toHaveLength(0);
  });
});
