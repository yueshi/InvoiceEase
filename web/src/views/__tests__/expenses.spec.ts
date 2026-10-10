import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExpensesView from "../ExpensesView.vue";
import { allowanceAmount } from "../../types";

vi.mock("../../api/receipts", () => ({
  listReceipts: vi.fn().mockResolvedValue([]),
}));

vi.mock("../../api/expenses", () => ({
  listClaims: vi.fn().mockResolvedValue([
    { id: 1, claim_no: "FY-202609-0001", title: "6 月差旅", total_amount: "272.50",
      status: "draft", applicant_id: 1, item_count: 1, submitted_at: null, decided_at: null,
      approver_id: null, rejected_reason: null, remark: null, created_at: "2026-09-13T00:00:00" },
  ]),
  getExpenseConfig: vi.fn().mockResolvedValue({
    travel_allowance_daily_standard: 120, petty_cash_threshold: 500,
    large_amount_threshold: "5000",
  }),
  createClaim: vi.fn(),
  getClaim: vi.fn(),
  eligibleInvoices: vi.fn().mockResolvedValue([]),
  addInvoiceToClaim: vi.fn(),
  addVoucherToClaim: vi.fn(),
  addReceiptToClaim: vi.fn(),
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

  it("渲染报销管理页面并加载报销单", async () => {
    const wrapper = mount(ExpensesView);
    expect(wrapper.exists()).toBe(true);
    expect(wrapper.text()).toContain("报销管理");
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

// ---- 无票凭证 / 引用回单入口（PoC 清单 A2，2026-09-19） ----------------------

/** 打开一张草稿单详情（含 1 个事项），返回已挂载的 wrapper */
async function openDraftDetail() {
  const api = await import("../../api/expenses");
  vi.mocked(api.getClaim).mockResolvedValue({
    claim: {
      id: 1, claim_no: "FY-202609-0001", title: "6 月差旅", total_amount: "0.00",
      status: "draft", applicant_id: 1, item_count: 0, submitted_at: null, decided_at: null,
      approver_id: null, rejected_reason: null, remark: null, created_at: "2026-09-13T00:00:00",
      claim_type: "travel",
    },
    entries: [{
      id: 11, claim_id: 1, entry_type: "travel", title: "上海→北京 高铁",
      occurred_on: "2026-06-10", scene_fields: null, amount: "0.00", note: null, items: [],
    }],
    items: [],
  } as never);
  const wrapper = await mountWithAntd();
  await flushPromises();
  const detailLink = wrapper.findAll("a").find((a) => a.text() === "详情");
  await detailLink!.trigger("click");
  await flushPromises();
  return wrapper;
}

function setInput(placeholder: string, value: string) {
  const el = document.body.querySelector(`input[placeholder="${placeholder}"]`) as HTMLInputElement;
  expect(el, `未找到输入框「${placeholder}」`).toBeTruthy();
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

/** 按标题定位弹窗（body 里可能同时存在多个已挂载的 modal，取第一个会点错） */
function modalByTitle(titleIncludes: string): Element {
  const modal = [...document.body.querySelectorAll(".ant-modal")].find((m) =>
    m.querySelector(".ant-modal-title")?.textContent?.includes(titleIncludes),
  );
  expect(modal, `未找到弹窗「${titleIncludes}」`).toBeTruthy();
  return modal!;
}

function clickModalOk(titleIncludes: string) {
  // 按 primary 定位而非文案：测试未装 zh_CN locale（按钮是 OK/Cancel，应用里是 确定/取消）
  const ok = modalByTitle(titleIncludes).querySelector(".ant-modal-footer .ant-btn-primary") as HTMLElement | null;
  expect(ok, "未找到弹窗确定按钮").toBeTruthy();
  ok!.click();
}

describe("报销入口补齐（无票凭证 / 引用回单）", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("草稿单事项行有「无票凭证」入口；收款凭证缺要素不发请求", async () => {
    await openDraftDetail();
    const api = await import("../../api/expenses");

    const btn = [...document.body.querySelectorAll("button")].find((b) => b.textContent === "无票凭证");
    expect(btn).toBeTruthy();
    btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();

    // 默认收款凭证：只填金额、不填收款人要素 → 本地拦截，不发请求
    setInput("如 300.00", "300");
    await flushPromises();
    clickModalOk("录入无票支出凭证");
    await flushPromises();
    expect(api.addVoucherToClaim).not.toHaveBeenCalled();
    expect(document.body.textContent).toContain("收款凭证需填写收款人姓名与身份证号");
  });

  it("收款凭证要素齐全 → 提交成功；返回不可扣除时当场提示原因", async () => {
    const api = await import("../../api/expenses");
    vi.mocked(api.addVoucherToClaim).mockResolvedValue({
      id: 99, claim_id: 1, invoice_id: null, receipt_id: null, voucher_type: "receipt_voucher",
      amount: "300.00", expense_type: "other", note: null, payee_name: "张三",
      payee_id_no: "110101199001011234", deductible: false,
      deductible_note: "单次 300.00 元已超小额零星标准（100 元），需取得发票方可税前扣除",
      active: true,
    } as never);

    await openDraftDetail();
    const btn = [...document.body.querySelectorAll("button")].find((b) => b.textContent === "无票凭证");
    btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();

    setInput("如 300.00", "300");
    setInput("个人收款人真实姓名", "张三");
    setInput("18 位身份证号", "110101199001011234");
    await flushPromises();
    clickModalOk("录入无票支出凭证");
    await flushPromises();
    await new Promise((r) => setTimeout(r, 50));

    expect(api.addVoucherToClaim).toHaveBeenCalledWith(1, 11, expect.objectContaining({
      voucher_type: "receipt_voucher", amount: "300",
      payee_name: "张三", payee_id_no: "110101199001011234",
    }));
    // 提示前置：不可扣除的原因当场给出（antd message 渲染到 body）
    expect(document.body.textContent).toContain("不可税前扣除");
    expect(document.body.textContent).toContain("需取得发票");
  });

  it("「引用回单」仅财务可见；选中后调接口并刷新", async () => {
    const { useAuthStore } = await import("../../stores/auth");
    const receiptsApi = await import("../../api/receipts");
    vi.mocked(receiptsApi.listReceipts).mockResolvedValue([{
      id: 7, file_url: "f.pdf", file_type: "PDF", trade_date: "2026-08-06",
      counterparty_name: "中国建设银行", amount: "15.00", abstract: "手续费",
      direction: "付", needs_review: false, quality_issues: null,
      category: "bank_fee", category_source: "rule", invoice_requirement: "none",
      bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
      status: "unmatched", created_at: "2026-08-06T10:00:00",
    }] as never);

    // 员工：无「引用回单」入口
    const emp = await openDraftDetail();
    expect([...document.body.querySelectorAll("button")].some((b) => b.textContent === "引用回单")).toBe(false);
    emp.unmount();
    document.body.innerHTML = "";

    // 财务：入口可见 → 选号器加载未配对回单 → 选中提交
    const auth = useAuthStore();
    auth.user = {
      id: 2, username: "caiwu", role: "finance_staff", status: "active",
      must_change_password: false, created_at: "2026-01-01T00:00:00",
    };
    const api = await import("../../api/expenses");
    vi.mocked(api.addReceiptToClaim).mockResolvedValue({
      id: 100, claim_id: 1, invoice_id: null, receipt_id: 7, voucher_type: "bank_receipt",
      amount: "15.00", expense_type: "other", note: null, payee_name: null, payee_id_no: null,
      deductible: true, deductible_note: null, active: true,
    } as never);

    await openDraftDetail();
    const btn = [...document.body.querySelectorAll("button")].find((b) => b.textContent === "引用回单");
    expect(btn).toBeTruthy();
    btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();

    expect(receiptsApi.listReceipts).toHaveBeenCalledWith({}, true); // 全部时间的未配对回单
    const receiptModal = modalByTitle("引用回单作为报销凭证");
    const radio = receiptModal.querySelector(".ant-table-tbody .ant-radio-input") as HTMLElement;
    expect(radio, "未渲染回单选号行").toBeTruthy();
    radio.click();
    await flushPromises();
    clickModalOk("引用回单作为报销凭证");
    await flushPromises();
    await new Promise((r) => setTimeout(r, 50));

    expect(api.addReceiptToClaim).toHaveBeenCalledWith(1, 11, { receipt_id: 7 });
  });
});

// ---- P0-2 / v1.1 §7.5.1：大额提交需二次确认（必填理由）--------------------

describe("ExpensesView 大额二次确认（v1.1 §7.5.1）", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    // 抽屉/弹窗 teleport 到 body 且不随 wrapper.unmount 清除；清掉防跨用例串target
    document.body.innerHTML = "";
  });

  function mockClaim(totalAmount: string) {
    const claim = {
      id: 7, claim_no: "FY-202610-0007", title: "大额差旅", total_amount: totalAmount,
      status: "draft", applicant_id: 1, item_count: 1, submitted_at: null,
      decided_at: null, approver_id: null, rejected_reason: null, remark: null,
      created_at: "2026-10-01T00:00:00", claim_type: "travel",
    };
    return { claim, entries: [], items: [] };
  }

  it("金额 ≥ 阈值：先弹确认（必填理由），确认后才提交", async () => {
    const api = await import("../../api/expenses");
    vi.mocked(api.getClaim).mockResolvedValue(mockClaim("8000.00") as never);
    vi.mocked(api.submitClaim).mockClear().mockResolvedValue(undefined as never);

    const wrapper = await mountWithAntd();
    await flushPromises();

    const detailLink = wrapper.findAll("a").find((a) => a.text() === "详情");
    await detailLink!.trigger("click");
    await flushPromises();

    clickInBody("button", "提交审批");
    await flushPromises();

    // ConfirmModal 未经 teleport，直接在组件树里查
    expect(wrapper.find(".cm-mask").exists()).toBe(true);
    expect(wrapper.find(".cm-title").text()).toContain("大额");
    expect(api.submitClaim).not.toHaveBeenCalled(); // 未确认不提交

    // 未填理由不可确认
    expect(wrapper.find("button.cm-confirm").attributes("disabled")).toBeDefined();
    await wrapper.find("textarea.cm-reason").setValue("董事会已批准");
    await wrapper.find("button.cm-confirm").trigger("click");
    await flushPromises();

    // 理由必须随提交传下去（v1.1 §7.2 ✅4「为什么」落审计）
    expect(api.submitClaim).toHaveBeenCalledWith(7, "董事会已批准");
  });

  it("金额 < 阈值：不弹确认，直接提交", async () => {
    const api = await import("../../api/expenses");
    vi.mocked(api.getClaim).mockResolvedValue(mockClaim("800.00") as never);
    vi.mocked(api.submitClaim).mockClear().mockResolvedValue(undefined as never);

    const wrapper = await mountWithAntd();
    await flushPromises();

    const detailLink = wrapper.findAll("a").find((a) => a.text() === "详情");
    await detailLink!.trigger("click");
    await flushPromises();

    clickInBody("button", "提交审批");
    await flushPromises();

    expect(wrapper.find(".cm-mask").exists()).toBe(false);
    expect(api.submitClaim).toHaveBeenCalledWith(7, undefined);
  });
});
