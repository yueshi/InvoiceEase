import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExpensesView from "../ExpensesView.vue";

vi.mock("../../api/expenses", () => ({
  listClaims: vi.fn().mockResolvedValue([
    { id: 1, claim_no: "FY-202609-0001", title: "6 月差旅", total_amount: "272.50",
      status: "draft", applicant_id: 1, item_count: 1, submitted_at: null, decided_at: null,
      approver_id: null, rejected_reason: null, remark: null, created_at: "2026-09-13T00:00:00" },
  ]),
  createClaim: vi.fn(),
  getClaim: vi.fn(),
  eligibleInvoices: vi.fn().mockResolvedValue([]),
  addInvoiceToClaim: vi.fn(),
  addVoucherToClaim: vi.fn(),
  removeItem: vi.fn(),
  submitClaim: vi.fn(),
  approveClaim: vi.fn(),
  rejectClaim: vi.fn(),
  withdrawClaim: vi.fn(),
}));

describe("ExpensesView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("渲染我的报销页面并加载报销单", async () => {
    const wrapper = mount(ExpensesView);
    expect(wrapper.exists()).toBe(true);
    expect(wrapper.text()).toContain("我的报销");
    const { listClaims } = await import("../../api/expenses");
    await new Promise((r) => setTimeout(r, 0));
    expect(listClaims).toHaveBeenCalled();
  });
});
