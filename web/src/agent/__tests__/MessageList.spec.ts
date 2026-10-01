// web/src/agent/__tests__/MessageList.spec.ts
import { mount, type VueWrapper } from "@vue/test-utils";
import { beforeAll, describe, expect, it } from "vitest";
import MessageList from "../components/MessageList.vue";
import type { AgentBlock } from "../types";

beforeAll(() => {
  // jsdom 24 无 Element.prototype.scrollTo；MessageList 的滚动 watcher 在改 props 的用例中会调用
  (Element.prototype as any).scrollTo = () => {};
});

/** DOM 顺序断言助手：按文档顺序返回三类节点的标签名 */
function blockOrder(wrapper: VueWrapper): string[] {
  return [...wrapper.element.querySelectorAll(".reasoning, .tool-chips, .md-body")].map((n) =>
    n.classList.contains("reasoning")
      ? "reasoning"
      : n.classList.contains("tool-chips")
        ? "tool-chips"
        : "md-body",
  );
}

describe("MessageList", () => {
  it("旧数据（无 blocks）兼容：渲染消息文本与工具 chip（含失败态）", () => {
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
        streamingBlocks: [],
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
        streamingBlocks: [],
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
        streamingBlocks: [],
        streaming: false,
      },
    });
    const bubble = wrapper.find(".msg.user");
    expect(bubble.text()).toContain("含 **星号** 文本"); // 星号原样，未被吃掉
    expect(bubble.find("strong").exists()).toBe(false);
    expect(bubble.find(".md-body").exists()).toBe(false); // 用户气泡不走 markdown 渲染容器
  });

  it("时间线 blocks：DOM 节点顺序与块序一致（5 块交错）", () => {
    const blocks: AgentBlock[] = [
      { type: "reasoning", text: "想1" },
      { type: "tool", tool: "invoice_stats", status: "done", ms: 15 },
      { type: "reasoning", text: "想2" },
      { type: "tool", tool: "invoice_list", status: "failed" },
      { type: "text", text: "最终回答" },
    ];
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 1, role: "assistant", content: "最终回答", tool_calls: null, blocks, created_at: "" },
        ],
        streamingBlocks: [],
        streaming: false,
      },
    });
    expect(
      [...wrapper.element.querySelectorAll(".reasoning, .tool-chips, .md-body")].length,
    ).toBe(5); // 每个工具块独立一行
    expect(blockOrder(wrapper)).toEqual([
      "reasoning", "tool-chips", "reasoning", "tool-chips", "md-body",
    ]);
    expect(wrapper.text()).toContain("invoice_stats · 完成 · 15ms");
    expect(wrapper.text()).toContain("invoice_list · 失败");
    expect(wrapper.find(".md-bubble").text()).toContain("最终回答");
  });

  it("旧数据兼容布局：reasoning + tool_calls + content（思考默认折叠可展开）", async () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          {
            id: 1, role: "assistant", created_at: "", content: "共 3 张",
            reasoning: "先查列表，再汇总。",
            tool_calls: [{ tool: "invoice_list", status: "done", ms: 12 }],
          },
        ],
        streamingBlocks: [],
        streaming: false,
      },
    });
    expect(blockOrder(wrapper)).toEqual(["reasoning", "tool-chips", "md-body"]);
    // 完成后默认折叠为一行
    expect(wrapper.find(".reasoning").text()).toContain("已深度思考");
    expect(wrapper.find(".reasoning-body").exists()).toBe(false);
    await wrapper.find(".reasoning-head").trigger("click");
    expect(wrapper.find(".reasoning-body").text()).toContain("先查列表，再汇总。");
  });

  it("流式时间线：思考 → 工具 chip → 正文，末块 text 时光标在正文尾", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [],
        streamingBlocks: [
          { type: "reasoning", text: "思考中文本" },
          { type: "tool", tool: "invoice_list", status: "done", ms: 5 },
          { type: "text", text: "正在汇总" },
        ],
        streaming: true,
      },
    });
    expect(blockOrder(wrapper)).toEqual(["reasoning", "tool-chips", "md-body"]);
    // 非末块：已翻篇的思考折叠展示
    expect(wrapper.find(".reasoning").text()).toContain("已深度思考");
    expect(wrapper.find(".md-bubble .cursor").exists()).toBe(true);
  });

  it("流式时间线：末块为思考时展开显示「思考中…」", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [],
        streamingBlocks: [{ type: "reasoning", text: "思考中文本" }],
        streaming: true,
      },
    });
    expect(wrapper.find(".reasoning").text()).toContain("思考中…");
    expect(wrapper.find(".reasoning-body").text()).toBe("思考中文本");
  });

  it("流式时间线：末块为工具时，光标独立追加在块列表尾", () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [],
        streamingBlocks: [{ type: "tool", tool: "invoice_list", status: "start" }],
        streaming: true,
      },
    });
    expect(wrapper.find(".md-bubble").exists()).toBe(false);
    expect(wrapper.find(".msg.assistant > .cursor").exists()).toBe(true);
  });

  it("思考内容纯文本渲染：标签/星号按字面显示，不注入 DOM", async () => {
    const evil = '<img src=x onerror="alert(1)"> **不该加粗**';
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 1, role: "assistant", created_at: "", content: "答", reasoning: evil, tool_calls: null },
        ],
        streamingBlocks: [],
        streaming: false,
      },
    });
    await wrapper.find(".reasoning-head").trigger("click");
    const body = wrapper.find(".reasoning-body");
    expect(body.element.querySelector("img")).toBeNull();
    expect(body.element.querySelector("strong")).toBeNull();
    expect(body.text()).toContain(evil);
  });

  it("流式期间显示光标（尚未产出任何块时独立显示）", () => {
    const wrapper = mount(MessageList, {
      props: { messages: [], streamingBlocks: [], streaming: true },
    });
    expect(wrapper.text()).toContain("▍"); // 流式光标
  });
});
