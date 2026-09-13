// 结构防回归：高亮层必须与 img 同处「图片尺寸包裹层」内——若包裹层带
// max-height（滚动容器直接当定位父级），百分比坐标会相对被截断的高度解析，
// 高亮被压缩/移位（真实 Chrome 实测偏差 16.5 个百分点）
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ReceiptLocatePanel from "../ReceiptLocatePanel.vue";

const BBOX: [number, number, number, number] = [0.03, 0.6933, 0.97, 0.9646];

describe("ReceiptLocatePanel", () => {
  it("高亮层与图片同父，且父级为相对定位的图片尺寸包裹层", () => {
    const wrapper = mount(ReceiptLocatePanel, {
      props: { imageUrl: "blob:fake", bbox: BBOX },
    });
    const img = wrapper.find("img");
    const hl = wrapper.find(".receipt-anchor-highlight");
    expect(img.exists()).toBe(true);
    expect(hl.exists()).toBe(true);

    const frame = wrapper.find(".receipt-locate-frame");
    expect(frame.exists()).toBe(true);
    // 同父：定位基准即图片本身
    expect(img.element.parentElement).toBe(frame.element);
    expect(hl.element.parentElement).toBe(frame.element);
    // 包裹层自身不得有 max-height（否则高度被截断，百分比坐标失真）
    expect(frame.attributes("style") || "").not.toContain("max-height");
    // 滚动容器在更外层
    expect(frame.element.parentElement?.getAttribute("style")).toContain("max-height");
  });

  it("归一化 bbox → CSS 百分比（y 轴翻转）", () => {
    const wrapper = mount(ReceiptLocatePanel, { props: { imageUrl: "blob:fake", bbox: BBOX } });
    const style = wrapper.find(".receipt-anchor-highlight").attributes("style") || "";
    expect(style).toContain("left: 3%");
    expect(style).toContain("top: 3.54%"); // (1-0.9646)*100
    expect(style).toContain("height: 27.13%"); // (0.9646-0.6933)*100
  });

  it("无 bbox 时不渲染高亮层（降级为纯页面图）", () => {
    const wrapper = mount(ReceiptLocatePanel, { props: { imageUrl: "blob:fake", bbox: null } });
    expect(wrapper.find("img").exists()).toBe(true);
    expect(wrapper.find(".receipt-anchor-highlight").exists()).toBe(false);
  });
});
