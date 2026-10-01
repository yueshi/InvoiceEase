// AgentChart「展开查看」交互：遮罩出现/关闭（Esc 与 ✕）；jsdom 无 canvas，mock echarts。
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("echarts/core", () => {
  const makeInst = () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() });
  return { use: vi.fn(), init: vi.fn(() => makeInst()) };
});
vi.mock("echarts/charts", () => ({ PieChart: {}, BarChart: {}, RadarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {}, TooltipComponent: {}, LegendComponent: {}, TitleComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

import AgentChart from "../components/AgentChart.vue";

const SPEC = { title: "费用构成", data: [{ name: "travel", value: 1 }] };

describe("AgentChart 展开查看", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("点击展开 → 全屏遮罩出现；Esc 与 ✕ 均可关闭", async () => {
    const wrapper = mount(AgentChart, {
      props: { kind: "pie" as const, spec: SPEC },
      attachTo: document.body,
    });
    expect(document.body.querySelector(".chart-overlay")).toBeNull();

    await wrapper.find(".expand-btn").trigger("click");
    await flushPromises();
    // Teleport 到 body（jsdom 查 document，而非 wrapper）
    expect(document.body.querySelector(".chart-overlay")).not.toBeNull();
    expect(document.body.querySelector(".chart-big")).not.toBeNull();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(document.body.querySelector(".chart-overlay")).toBeNull();

    // 再开一次：用关闭按钮
    await wrapper.find(".expand-btn").trigger("click");
    await flushPromises();
    (document.body.querySelector(".chart-panel-close") as HTMLElement).click();
    await flushPromises();
    expect(document.body.querySelector(".chart-overlay")).toBeNull();

    wrapper.unmount();
  });

  it("loading 骨架态不渲染展开按钮", () => {
    const wrapper = mount(AgentChart, {
      props: { kind: "pie" as const, spec: SPEC, loading: true },
    });
    expect(wrapper.find(".expand-btn").exists()).toBe(false);
    wrapper.unmount();
  });
});
