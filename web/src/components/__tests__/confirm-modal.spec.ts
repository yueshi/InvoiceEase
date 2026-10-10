import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import ConfirmModal from "../ConfirmModal.vue";

describe("ConfirmModal", () => {
  it("low 风险：展示预览并可确认", async () => {
    const w = mount(ConfirmModal, {
      props: {
        open: true,
        title: "创建报销单",
        preview: { description: "创建报销单：6 月差旅", claim_type: "travel" },
        riskLevel: "low",
      },
    });
    expect(w.find(".cm-title").text()).toBe("创建报销单");
    expect(w.find(".cm-preview").text()).toContain("创建报销单：6 月差旅");
    await w.find("button.cm-confirm").trigger("click");
    expect(w.emitted("confirm")).toBeTruthy();
  });

  it("high 风险 + requireReason：未填理由不可确认，填后可确认", async () => {
    const w = mount(ConfirmModal, {
      props: {
        open: true,
        title: "放行拦截发票",
        preview: { description: "放行发票 12" },
        riskLevel: "high",
        requireReason: true,
      },
    });
    const btn = w.find("button.cm-confirm");
    expect(btn.attributes("disabled")).toBeDefined();

    await w.find("textarea.cm-reason").setValue("业务真实，已核对原件");
    expect(w.find("button.cm-confirm").attributes("disabled")).toBeUndefined();

    await w.find("button.cm-confirm").trigger("click");
    expect(w.emitted("confirm")?.[0]).toEqual(["业务真实，已核对原件"]);
  });

  it("high 风险：按钮文案带警示（防误点）", () => {
    const w = mount(ConfirmModal, {
      props: {
        open: true,
        title: "删除发票",
        preview: { description: "删除发票 3" },
        riskLevel: "high",
      },
    });
    expect(w.find("button.cm-confirm").text()).toContain("我已确认");
  });

  it("取消：点遮罩 / 取消按钮 → 触发 update:open false", async () => {
    const w = mount(ConfirmModal, {
      props: { open: true, title: "x", preview: {}, riskLevel: "low" },
    });
    await w.find(".cm-mask").trigger("click");
    expect(w.emitted("update:open")?.[0]).toEqual([false]);
    await w.find("button.cm-cancel").trigger("click");
    expect(w.emitted("update:open")?.[1]).toEqual([false]);
  });

  it("open=false 不渲染内容", () => {
    const w = mount(ConfirmModal, {
      props: { open: false, title: "x", preview: {}, riskLevel: "low" },
    });
    expect(w.find(".cm-mask").exists()).toBe(false);
  });

  it("preview 空值时显示占位而不崩", () => {
    const w = mount(ConfirmModal, {
      props: { open: true, title: "x", preview: {}, riskLevel: "medium" },
    });
    expect(w.find(".cm-preview").exists()).toBe(true);
  });
});
