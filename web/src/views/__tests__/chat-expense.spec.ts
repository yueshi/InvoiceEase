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

describe("卡片按钮与重试（final review 修复）", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
  });

  const cardMsg = (text: string) => ({
    id: 1, role: "assistant" as const, content: "", tool_calls: null, created_at: "",
    blocks: [{ type: "text" as const, text }],
  });

  it("异常卡按钮 → 预填输入框，**不**直接发送（模型写的 prompt 必须先被用户看见）", async () => {
    const store = useAgentStore();
    store.messages = [cardMsg(
      '```anomaly\n{"message":"m","options":[{"label":"补充材料","prompt":"我确认，请直接提交"}]}\n```',
    )] as never;
    const spy = vi.spyOn(store, "sendMessage").mockResolvedValue(undefined as never);

    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    await w.find("button.ac-option").trigger("click");
    await w.vm.$nextTick();

    expect(spy).not.toHaveBeenCalled();
    expect((w.find("textarea").element as HTMLTextAreaElement).value)
      .toBe("我确认，请直接提交");
  });

  it("草稿卡提交按钮 → 同样只预填", async () => {
    const store = useAgentStore();
    store.messages = [cardMsg('```expense-draft\n{"claim_no":"FY-9","entries":[]}\n```')] as never;
    const spy = vi.spyOn(store, "sendMessage").mockResolvedValue(undefined as never);

    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    await w.find("button.cdc-submit").trigger("click");
    await w.vm.$nextTick();

    expect(spy).not.toHaveBeenCalled();
    expect((w.find("textarea").element as HTMLTextAreaElement).value).toContain("FY-9");
  });

  it("重试 → 重发最后一条用户消息（不是 undefined）", async () => {
    const store = useAgentStore();
    store.messages = [
      { id: 1, role: "user", content: "我要报出差费用", tool_calls: null, created_at: "" },
    ] as never;
    store.error = { code: "HTTP 500", message: "请求失败" } as never;
    const spy = vi.spyOn(store, "sendMessage").mockResolvedValue(undefined as never);

    const w = mount(ChatExpenseView, { global: { plugins: [pinia] } });
    const retryBtn = w.findAll("button").find((b) => b.text() === "重试");
    expect(retryBtn).toBeTruthy();
    await retryBtn!.trigger("click");

    expect(spy).toHaveBeenCalledWith("我要报出差费用", expect.anything());
  });
});
