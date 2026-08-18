import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ReceiptsView from "../ReceiptsView.vue";

vi.mock("../../api/receipts", () => ({
  listReceipts: vi.fn().mockResolvedValue([]),
  uploadReceipt: vi.fn(),
  autoPairReceipt: vi.fn(),
  exportReceipts: vi.fn(),
}));

describe("ReceiptsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("渲染回单页面", () => {
    const wrapper = mount(ReceiptsView);
    expect(wrapper.exists()).toBe(true);
    expect(wrapper.text()).toContain("银行回单");
  });

  it("加载时调用 listReceipts", async () => {
    mount(ReceiptsView);
    const { listReceipts } = await import("../../api/receipts");
    await new Promise((r) => setTimeout(r, 0));
    expect(listReceipts).toHaveBeenCalled();
  });
});
