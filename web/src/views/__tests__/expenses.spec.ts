import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExpensesView from "../ExpensesView.vue";
import { allowanceAmount } from "../../types";

vi.mock("../../api/expenses", () => ({
  listClaims: vi.fn().mockResolvedValue([
    { id: 1, claim_no: "FY-202609-0001", title: "6 月差旅", total_amount: "272.50",
      status: "draft", applicant_id: 1, item_count: 1, submitted_at: null, decided_at: null,
      approver_id: null, rejected_reason: null, remark: null, created_at: "2026-09-13T00:00:00" },
  ]),
  getExpenseConfig: vi.fn().mockResolvedValue({ travel_allowance_daily_standard: 120 }),
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

/** 断言弹窗内的提示文案需真实渲染 Antd（未注册时组件不解析，属性不进 DOM） */
async function mountWithAntd() {
  // Antd 响应式组件依赖 matchMedia（jsdom 无此 API）
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addListener: () => {}, removeListener: () => {},
        addEventListener: () => {}, removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
  const Antd = (await import("ant-design-vue")).default;
  return mount(ExpensesView, { global: { plugins: [Antd] } });
}

/** 在 teleport 到 body 的抽屉/弹窗里按文字找元素并点击 */
function clickInBody(selector: string, text: string) {
  const el = [...document.body.querySelectorAll(selector)].find((e) =>
    e.textContent?.includes(text),
  ) as HTMLElement | undefined;
  if (!el) throw new Error(`未找到「${text}」(${selector})`);
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

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

  it("挂载时拉取报销配置（补助日标准预填用，P1-3）", async () => {
    mount(ExpensesView);
    const { getExpenseConfig } = await import("../../api/expenses");
    await new Promise((r) => setTimeout(r, 0));
    expect(getExpenseConfig).toHaveBeenCalled();
  });

  it("选「伙食补助」预填公司日标准，填天数即出金额预览（P1-3）", async () => {
    const api = await import("../../api/expenses");
    vi.mocked(api.getClaim).mockResolvedValue({
      claim: {
        id: 1, claim_no: "FY-202609-0001", title: "6 月差旅", total_amount: "0.00",
        status: "draft", applicant_id: 1, item_count: 0, submitted_at: null, decided_at: null,
        approver_id: null, rejected_reason: null, remark: null, created_at: "2026-09-13T00:00:00",
        claim_type: "travel",
      },
      entries: [],
      items: [],
    });

    const wrapper = await mountWithAntd();
    await flushPromises();

    // 打开详情抽屉（抽屉/弹窗都 teleport 到 body，之后一律查 document）
    const detailLink = wrapper.findAll("a").find((a) => a.text() === "详情");
    await detailLink!.trigger("click");
    await flushPromises();
    clickInBody("button", "添加事项");
    await flushPromises();

    // 差旅子类 → 伙食补助（第 2 个 Select：第 1 个是事项类型）
    const selects = document.body.querySelectorAll(".ant-select");
    expect(selects.length).toBeGreaterThanOrEqual(2);
    (selects[1].querySelector(".ant-select-selector") as HTMLElement).dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true }),
    );
    await flushPromises();
    const option = [...document.body.querySelectorAll(".ant-select-item-option")].find((o) =>
      o.textContent?.includes("伙食补助"),
    );
    expect(option).toBeTruthy();
    option!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();

    expect(document.body.textContent).toContain("120"); // 预填公司标准
    // 填天数 → 实时预览 4 × 120 = 480.00
    const dayInput = [...document.body.querySelectorAll("input")].find((i) =>
      i.closest(".ant-form-item")?.textContent?.includes("补助天数"),
    ) as HTMLInputElement | undefined;
    expect(dayInput).toBeTruthy();
    dayInput!.value = "4";
    dayInput!.dispatchEvent(new Event("input"));
    await flushPromises();
    expect(document.body.textContent).toContain("480.00");
  }, 20000); // 注册全量 Antd 后并行跑会超默认 5s
});

describe("allowanceAmount（补助金额算式，与后端一致）", () => {
  it("天数 × 日标准，量化到分", () => {
    expect(allowanceAmount("4", "100")).toBe("400.00");
    expect(allowanceAmount(2, 80.5)).toBe("161.00");
    expect(allowanceAmount("1.5", "100")).toBe("150.00");
  });

  it("非正数/非数字 → 空串（预览不显示金额，避免误导）", () => {
    expect(allowanceAmount("", "100")).toBe("");
    expect(allowanceAmount("0", "100")).toBe("");
    expect(allowanceAmount("两天", "100")).toBe("");
    expect(allowanceAmount("2", "-1")).toBe("");
  });
});
