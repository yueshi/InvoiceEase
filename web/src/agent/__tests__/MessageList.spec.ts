// web/src/agent/__tests__/MessageList.spec.ts
import { mount } from "@vue/test-utils";
import { beforeAll, describe, expect, it } from "vitest";
import MessageList from "../components/MessageList.vue";

beforeAll(() => {
  // jsdom 24 无 Element.prototype.scrollTo；MessageList 的滚动 watcher 在改 props 的用例中会调用
  (Element.prototype as any).scrollTo = () => {};
});

describe("MessageList", () => {
  it("渲染消息文本与工具 chip（含失败态）", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 1, role: "user", content: "查发票", tool_calls: null, created_at: "" },
          {
            id: 2, role: "assistant", content: "共 3 张", created_at: "",
            tool_calls: [
              { tool: "invoice_list", status: "done", ms: 12 },
              { tool: "invoice_detail", status: "failed" },
            ],
          },
        ],
        streamingText: "",
        streamingTools: [],
        streaming: false,
      },
    });
    expect(wrapper.text()).toContain("查发票");
    expect(wrapper.text()).toContain("共 3 张");
    expect(wrapper.text()).toContain("invoice_list · 完成 · 12ms");
    expect(wrapper.text()).toContain("invoice_detail · 失败");
  });

  it("助手消息走 markdown，非图表 fence 走代码块（用户消息仍纯文本）", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          {
            id: 1, role: "assistant", created_at: "", tool_calls: null,
            content: "**重点**\n```json\n{}\n```",
          },
        ],
        streamingText: "",
        streamingTools: [],
        streaming: false,
      },
    });
    expect(wrapper.find(".md-bubble strong").text()).toBe("重点");
    expect(wrapper.find("pre.code-block").text()).toBe("{}");
  });

  it("用户消息原样纯文本：** 不渲染为 markdown", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 1, role: "user", content: "含 **星号** 文本", tool_calls: null, created_at: "" },
        ],
        streamingText: "",
        streamingTools: [],
        streaming: false,
      },
    });
    const bubble = wrapper.find(".msg.user");
    expect(bubble.text()).toContain("含 **星号** 文本"); // 星号原样，未被吃掉
    expect(bubble.find("strong").exists()).toBe(false);
    expect(bubble.find(".md-body").exists()).toBe(false); // 用户气泡不走 markdown 渲染容器
  });

  it("流式期间显示光标", () => {
    const wrapper = mount(MessageList, {
      props: { messages: [], streamingText: "正在", streamingTools: [], streaming: true },
    });
    expect(wrapper.text()).toContain("正在");
    expect(wrapper.text()).toContain("▍"); // 流式光标
  });
});
