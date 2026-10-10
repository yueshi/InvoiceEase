// 草稿预览卡测试（P2 §6.2「报销单预览确认」）
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ClaimDraftCard from "../ClaimDraftCard.vue";

const CARD = {
  claim_no: "FY-202610-0007", title: "上海出差", claim_type: "travel",
  total_amount: "1234.56", status: "draft",
  entries: [{ title: "高铁", amount: "553.00" }, { title: "住宿", amount: "681.56" }],
};

describe("ClaimDraftCard", () => {
  it("展示单号/事由/金额/明细", () => {
    const w = mount(ClaimDraftCard, { props: { card: CARD } });
    expect(w.text()).toContain("FY-202610-0007");
    expect(w.text()).toContain("上海出差");
    expect(w.text()).toContain("1234.56");
    expect(w.text()).toContain("高铁");
    expect(w.text()).toContain("681.56");
  });

  it("点「确认提交」→ emit submit（带单号的预置提示词，防指代不明）", async () => {
    const w = mount(ClaimDraftCard, { props: { card: CARD } });
    await w.find("button.cdc-submit").trigger("click");
    const [prompt] = w.emitted("submit")![0] as [string];
    expect(prompt).toContain("提交");
    expect(prompt).toContain("FY-202610-0007");
  });

  it("缺金额时不显示合计行（不显示 0.00 骗人）", () => {
    const w = mount(ClaimDraftCard, { props: { card: { entries: [] } } });
    expect(w.find(".cdc-total").exists()).toBe(false);
  });

  it("明细为空时显示占位", () => {
    const w = mount(ClaimDraftCard, { props: { card: { entries: [] } } });
    expect(w.text()).toContain("暂无明细");
  });

  it("金额为字符串原样展示（含千分位也不改写）", () => {
    const w = mount(ClaimDraftCard, {
      props: { card: { total_amount: "1,234.56", entries: [] } },
    });
    expect(w.text()).toContain("1,234.56");
  });

  it("状态非 draft 时不显示提交按钮（已提交的单不能再提）", () => {
    const w = mount(ClaimDraftCard, {
      props: { card: { ...CARD, status: "pending_approval" } },
    });
    expect(w.find("button.cdc-submit").exists()).toBe(false);
  });
});
