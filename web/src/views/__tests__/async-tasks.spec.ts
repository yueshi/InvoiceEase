import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AsyncTasksView from "../AsyncTasksView.vue";

vi.mock("../../api/receipts", () => ({
  listReceiptUploads: vi.fn().mockResolvedValue([
    { id: 1, status: "parsing", receipt_count: 0, error: null, created_at: "2026-09-12T10:00:00", parsed_at: null },
  ]),
}));

describe("AsyncTasksView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("渲染异步任务页面并加载批次", async () => {
    const wrapper = mount(AsyncTasksView);
    expect(wrapper.exists()).toBe(true);
    expect(wrapper.text()).toContain("异步任务");
    const { listReceiptUploads } = await import("../../api/receipts");
    await new Promise((r) => setTimeout(r, 0));
    expect(listReceiptUploads).toHaveBeenCalled();
  });
});
