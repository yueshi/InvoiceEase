// 异常卡测试（P2 §6.2「异常提示与处理」：醒目展示原因 + 三选一处理项）
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AnomalyCard from "../AnomalyCard.vue";

const CARD = {
  kind: "OVER_STANDARD",
  message: "招待人均 450 元超公司标准 300 元（超出 150 元）",
  options: [
    { label: "补充材料", prompt: "我要补充材料" },
    { label: "申请特批", prompt: "帮我申请特批" },
    { label: "修改金额", prompt: "我要修改金额" },
  ],
};

describe("AnomalyCard", () => {
  it("默认展开展示原因与全部选项（异常必须被看见）", () => {
    const w = mount(AnomalyCard, { props: { card: CARD } });
    expect(w.text()).toContain("超公司标准");
    // 所见即所发：按钮显示的是**将要发出的那句话**（prompt），不是 label
    for (const o of CARD.options) expect(w.text()).toContain(o.prompt);
  });

  it("label 与 prompt 不同时，label 作 tooltip 保留（防按钮显示与实际发送不一致）", () => {
    const w = mount(AnomalyCard, { props: { card: CARD } });
    const btn = w.findAll("button.ac-option")[0];
    expect(btn.text()).toBe("我要补充材料");
    expect(btn.attributes("title")).toBe("补充材料");
  });

  it("点选项 → emit choose(该选项的 prompt)", async () => {
    const w = mount(AnomalyCard, { props: { card: CARD } });
    const btns = w.findAll("button.ac-option");
    expect(btns).toHaveLength(3);
    await btns[1].trigger("click");
    expect(w.emitted("choose")?.[0]).toEqual(["帮我申请特批"]);
  });

  it("无选项时只展示原因（不渲染空按钮区）", () => {
    const w = mount(AnomalyCard, { props: { card: { message: "缺返程票" } } });
    expect(w.find(".ac-options").exists()).toBe(false);
    expect(w.text()).toContain("缺返程票");
  });

  it("文案不含越权暗示（自动通过 / 帮你放行）", () => {
    const w = mount(AnomalyCard, { props: { card: CARD } });
    expect(w.text()).not.toContain("自动通过");
    expect(w.text()).not.toContain("帮你放行");
  });

  it("原因原样展示（不加工工具返回的文案）", () => {
    const msg = "行程未见返程：首段自 上海 出发，末段到达 北京";
    const w = mount(AnomalyCard, { props: { card: { message: msg } } });
    expect(w.text()).toContain(msg);
  });

  it("展示异常类型标签（有 kind 时）", () => {
    const w = mount(AnomalyCard, { props: { card: CARD } });
    expect(w.find(".ac-kind").exists()).toBe(true);
    expect(w.text()).toContain("OVER_STANDARD");
  });
});
