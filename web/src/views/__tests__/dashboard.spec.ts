import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DashboardView from "../DashboardView.vue";
import { useAuthStore } from "../../stores/auth";
import type { Role, UserOut } from "../../types";

vi.mock("../../api/stats", () => ({
  fetchOverview: vi.fn().mockResolvedValue({
    pending_review: 3, pending_submit: 10, today_new: 5, month_total: 88,
  }),
}));

function loginAs(role: Role) {
  const auth = useAuthStore();
  auth.user = {
    id: 1, username: "u1", role, status: "active", must_change_password: false,
    created_at: "2026-09-17T00:00:00",
  } satisfies UserOut;
}

const stubs = { global: { stubs: { "a-row": true, "a-col": true, "a-card": true, "a-statistic": true } } };

describe("DashboardView", () => {
  beforeEach(() => {
    setActivePinia(createPinia()); // setup 内 useAuthStore()，需先激活 Pinia
  });

  it("加载并展示统计数值", async () => {
    const wrapper = mount(DashboardView, stubs);
    await new Promise((r) => setTimeout(r, 0));
    const stats = (wrapper.vm as any).stats;
    expect(stats).toEqual({ pending_review: 3, pending_submit: 10, today_new: 5, month_total: 88 });
  });

  it("页头注明数据范围：员工=本人，财务=全公司（FRD §3.5.2 口径）", () => {
    loginAs("employee");
    expect(mount(DashboardView, stubs).text()).toContain("数据范围：本人");

    setActivePinia(createPinia());
    loginAs("finance_staff");
    expect(mount(DashboardView, stubs).text()).toContain("数据范围：全公司");
  });
});
