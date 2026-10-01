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

  it("流式期间显示光标", () => {
    const wrapper = mount(MessageList, {
      props: { messages: [], streamingText: "正在", streamingTools: [], streaming: true },
    });
    expect(wrapper.text()).toContain("正在");
    expect(wrapper.text()).toContain("▍"); // 流式光标
  });
});
