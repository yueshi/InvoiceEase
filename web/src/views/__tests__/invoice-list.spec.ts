import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import InvoiceListView from "../InvoiceListView.vue";

vi.mock("../../api/invoices", () => ({
  listInvoices: vi.fn().mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 20,
  }),
}));

describe("InvoiceListView", () => {
  beforeEach(() => {
    // 组件 setup 中调用 useAuthStore()（canReview 依赖角色），需先激活 Pinia
    setActivePinia(createPinia());
  });

  it("渲染筛选控件与表格", () => {
    const wrapper = mount(InvoiceListView, { global: { stubs: { InvoiceDetailDrawer: true } } });
    expect(wrapper.exists()).toBe(true);
  });

  it("首次加载调用 listInvoices 默认参数", async () => {
    const wrapper = mount(InvoiceListView, { global: { stubs: { InvoiceDetailDrawer: true } } });
    const { listInvoices } = await import("../../api/invoices");
    await new Promise((r) => setTimeout(r, 0));
    expect(listInvoices).toHaveBeenCalled();
  });
});
