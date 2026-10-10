// AssistantMarkdown 分段渲染回归：表格/图表 前后内容必须正常渲染
// （bug 报告：表格/图表后 markdown 未正常渲染）
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import AssistantMarkdown from "../components/AssistantMarkdown.vue";

const CHART_FENCE = '```chart-pie\n{"title":"t","data":[{"name":"x","value":1}]}\n```';

/** AgentChart 是 defineAsyncComponent（内含 echarts）：用 VTU 默认 stub 按名替换，
 *  不触发真实动态 import；默认 stub 渲染为 <agent-chart-stub>。 */
function mountMd(text: string, streaming = false) {
  return mount(AssistantMarkdown, {
    props: { text, streaming },
    global: { stubs: { AgentChart: true } },
  });
}

describe("AssistantMarkdown 表格/图表前后渲染", () => {
  it("表格后的正文不被吞成表格行（有/无空行两种模型输出习惯）", () => {
    const withBlank = "| 项目 | 金额 |\n|---|---|\n| 差旅 | 100 |\n\n后续 **重点** 说明";
    const noBlank = "| 项目 | 金额 |\n|---|---|\n| 差旅 | 100 |\n后续 **重点** 说明";
    for (const text of [withBlank, noBlank]) {
      const w = mountMd(text);
      expect(w.findAll("table")).toHaveLength(1);
      // 表格体只应有 1 行（差旅）；正文行若被吞成表格行会变 2 行
      expect(w.findAll("tbody tr")).toHaveLength(1);
      // 表格后应为独立段落并正常渲染 markdown
      expect(w.html()).toMatch(/<\/table>[\s\S]*<p>[^<]*后续 <strong>重点<\/strong>/);
      w.unmount();
    }
  });

  it("图表 fence 之后的 markdown 正常渲染", () => {
    const text = `说明如下：\n\n${CHART_FENCE}\n\n图表后的 **补充** 文字`;
    const w = mountMd(text);
    expect(w.find("agent-chart-stub").exists()).toBe(true); // VTU 默认 stub 是标签而非 class
    expect(w.html()).toMatch(/agent-chart-stub[\s\S]*<strong>补充<\/strong>/);
    w.unmount();
  });

  it("表格 → 图表 → 文字 混合流：三段各自渲染", () => {
    const text = `| a | b |\n|---|---|\n| 1 | 2 |\n\n${CHART_FENCE}\n\n结尾 **总结**`;
    const w = mountMd(text);
    const html = w.html();
    expect(html).toMatch(/<table>[\s\S]*agent-chart-stub[\s\S]*<strong>总结<\/strong>/);
    w.unmount();
  });

  it("完成后（streaming=false）全文完整渲染，不留纯文本余量", () => {
    const text = `| a | b |\n|---|---|\n| 1 | 2 |\n末尾 **粗体**`;
    const w = mountMd(text, false);
    expect(w.find(".md-tail").exists()).toBe(false);
    expect(w.html()).toContain("<strong>粗体</strong>");
    w.unmount();
  });
});

// ---- P2：结构化卡片（expense-draft / anomaly）----------------------------

describe("AssistantMarkdown 卡片接线", () => {
  it("expense-draft fence 渲染草稿卡而非代码块", () => {
    const w = mount(AssistantMarkdown, {
      props: { text: '```expense-draft\n{"claim_no": "FY-1", "entries": []}\n```' },
    });
    expect(w.find(".cdc-card").exists()).toBe(true);
    expect(w.find("pre.code-block").exists()).toBe(false);
  });

  it("anomaly fence 渲染异常卡", () => {
    const w = mount(AssistantMarkdown, {
      props: { text: '```anomaly\n{"message": "缺返程票"}\n```' },
    });
    expect(w.find(".ac-card").exists()).toBe(true);
  });

  it("半截 expense-draft JSON 退回代码块（不渲染半成品卡）", () => {
    const w = mount(AssistantMarkdown, {
      props: { text: '```expense-draft\n{"claim_no": "FY-1"' },
    });
    expect(w.find(".cdc-card").exists()).toBe(false);
  });

  it("卡片按钮事件冒泡为 chat-action（交给上层注入消息）", async () => {
    const w = mount(AssistantMarkdown, {
      props: { text: '```anomaly\n{"message":"m","options":[{"label":"A","prompt":"pa"}]}\n```' },
    });
    await w.find("button.ac-option").trigger("click");
    expect(w.emitted("chat-action")?.[0]).toEqual(["pa"]);
  });

  it("草稿卡提交按钮同样冒泡 chat-action", async () => {
    const w = mount(AssistantMarkdown, {
      props: { text: '```expense-draft\n{"claim_no":"FY-9","entries":[]}\n```' },
    });
    await w.find("button.cdc-submit").trigger("click");
    const [prompt] = w.emitted("chat-action")![0] as [string];
    expect(prompt).toContain("FY-9");
  });
});

describe("流式期间卡片按钮禁用（点击会被 store 静默吞掉）", () => {
  it("streaming=true 时异常卡按钮 disabled", () => {
    const w = mount(AssistantMarkdown, {
      props: {
        text: '```anomaly\n{"message":"m","options":[{"label":"A","prompt":"pa"}]}\n```',
        streaming: true,
      },
    });
    expect(w.find("button.ac-option").attributes("disabled")).toBeDefined();
  });

  it("streaming=true 时草稿卡提交按钮 disabled", () => {
    const w = mount(AssistantMarkdown, {
      props: {
        text: '```expense-draft\n{"claim_no":"FY-1","entries":[]}\n```',
        streaming: true,
      },
    });
    expect(w.find("button.cdc-submit").attributes("disabled")).toBeDefined();
  });

  it("非流式时可点", () => {
    const w = mount(AssistantMarkdown, {
      props: {
        text: '```anomaly\n{"message":"m","options":[{"label":"A","prompt":"pa"}]}\n```',
        streaming: false,
      },
    });
    expect(w.find("button.ac-option").attributes("disabled")).toBeUndefined();
  });
});
