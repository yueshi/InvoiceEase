import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import DashboardView from "../DashboardView.vue";

vi.mock("../../api/stats", () => ({
  fetchOverview: vi.fn().mockResolvedValue({
    pending_review: 3, pending_submit: 10, today_new: 5, month_total: 88,
  }),
}));

describe("DashboardView", () => {
  it("加载并展示统计数值", async () => {
    const wrapper = mount(DashboardView, { global: { stubs: { "a-row": true, "a-col": true, "a-card": true, "a-statistic": true } } });
    await new Promise((r) => setTimeout(r, 0));
    const stats = (wrapper.vm as any).stats;
    expect(stats).toEqual({ pending_review: 3, pending_submit: 10, today_new: 5, month_total: 88 });
  });
});
