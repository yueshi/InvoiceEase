import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReceiptsView from "../ReceiptsView.vue";
import type { ReceiptOut } from "../../types";

vi.mock("../../api/receipts", () => ({
  listReceipts: vi.fn().mockResolvedValue([]),
  probeReceiptsPeriod: vi.fn().mockResolvedValue(0),
  listReceiptUploads: vi.fn().mockResolvedValue([]),
  uploadReceipt: vi.fn(),
  autoPairReceipt: vi.fn(),
  confirmReceiptReview: vi.fn().mockResolvedValue({}),
  setReceiptCategory: vi.fn().mockResolvedValue({}),
  deleteReceipt: vi.fn().mockResolvedValue(undefined),
  exportReceipts: vi.fn(),
  fetchReceiptFileUrl: vi.fn(),
  fetchReceiptPageUrl: vi.fn(),
}));

const stubs = { global: { stubs: { ReceiptDetailDrawer: true, ReceiptLocatePanel: true } } };

/** 断言提示文案需真实渲染 a-alert（未注册 Antd 时 message 属性不进 DOM）；withDrawer 时不 stub 详情抽屉 */
async function mountWithAntd(withDrawer = false) {
  // Antd 响应式组件依赖 matchMedia（jsdom 无此 API）
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
  const Antd = (await import("ant-design-vue")).default;
  return mount(ReceiptsView, {
    global: {
      plugins: [Antd],
      stubs: { ...(withDrawer ? {} : { ReceiptDetailDrawer: true }), ReceiptLocatePanel: true },
    },
  });
}

describe("ReceiptsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  // 抽屉/message 的 DOM 挂在 body（teleport），不清会串到下一条用例的 body 断言
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("渲染回单页面", () => {
    const wrapper = mount(ReceiptsView, stubs);
    expect(wrapper.exists()).toBe(true);
    expect(wrapper.text()).toContain("银行回单");
  });

  it("加载时调用 listReceipts", async () => {
    mount(ReceiptsView, stubs);
    const { listReceipts } = await import("../../api/receipts");
    await new Promise((r) => setTimeout(r, 0));
    expect(listReceipts).toHaveBeenCalled();
  });

  it("当前周期无回单但其他周期有 → 提示条数并可切到「全部」（P1-4）", async () => {
    const api = await import("../../api/receipts");
    vi.mocked(api.listReceipts).mockResolvedValue([]);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(3);

    const wrapper = await mountWithAntd();
    await flushPromises();

    expect(wrapper.text()).toContain("其他周期有 3 条");
    const link = wrapper.findAll("a").filter((a) => a.text().includes("查看全部时间"));
    expect(link).toHaveLength(1);

    await link[0].trigger("click");
    await flushPromises();
    // 「全部」不做日期过滤：periodParam() 为空对象
    expect(vi.mocked(api.listReceipts)).toHaveBeenLastCalledWith({}, false);
  }, 20000); // 注册全量 Antd 后并行跑会超默认 5s

  it("待核对行有「核对无误」入口，点击调接口并刷新", async () => {
    const api = await import("../../api/receipts");
    vi.mocked(api.listReceipts).mockResolvedValue([
      {
        id: 7, file_url: "f.pdf", file_type: "PDF", trade_date: "2026-08-06",
        counterparty_name: "中国建设银行", amount: "15.00", abstract: "手续费",
        direction: "付", needs_review: true, quality_issues: ["self_account_row"],
        category: "bank_fee", category_source: "rule", invoice_requirement: "none",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-06T10:00:00",
      },
    ]);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(0);

    const wrapper = await mountWithAntd();
    await flushPromises();

    const link = wrapper.findAll("a").filter((a) => a.text().includes("核对无误"));
    expect(link).toHaveLength(1);
    await link[0].trigger("click");
    await flushPromises();
    expect(vi.mocked(api.confirmReceiptReview)).toHaveBeenCalledWith(7);
  }, 20000);

  it("未配对行按发票要求三分：无票 / 待开票 / 无需发票", async () => {
    const api = await import("../../api/receipts");
    vi.mocked(api.listReceipts).mockResolvedValue([
      {
        id: 1, file_url: "a.pdf", file_type: "PDF", trade_date: "2026-08-10",
        counterparty_name: "供应商甲", amount: "800.00", abstract: "货款",
        direction: "付", needs_review: false, quality_issues: null,
        category: "purchase", category_source: "rule", invoice_requirement: "fetch",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-10T10:00:00",
      },
      {
        id: 2, file_url: "b.pdf", file_type: "PDF", trade_date: "2026-08-07",
        counterparty_name: "国家金库陕西省西咸新区支库", amount: "1116.00", abstract: "税款",
        direction: "付", needs_review: false, quality_issues: null,
        category: "tax", category_source: "rule", invoice_requirement: "none",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-07T10:00:00",
      },
      {
        id: 3, file_url: "c.pdf", file_type: "PDF", trade_date: "2026-08-06",
        counterparty_name: "客户甲有限公司", amount: "5000.00", abstract: "货款",
        direction: "收", needs_review: false, quality_issues: null,
        category: "sales_collection", category_source: "rule", invoice_requirement: "issue",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-06T10:00:00",
      },
    ]);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(0);

    const wrapper = await mountWithAntd();
    await flushPromises();
    // 逐行断言标签归属：分支对调 / 行错配必红；集合式 toContain 杀不掉，
    // 且工具栏「只看无票支出」本就含「无票」
    const tags = wrapper.findAll(".ant-tag").map((t) => t.text());
    expect(tags).toEqual(["无票", "无需发票", "待开票"]); // 顺序即行的顺序
  }, 20000);

  it("详情抽屉标注交易性质来源（人工/规则）", async () => {
    const api = await import("../../api/receipts");
    const rows: ReceiptOut[] = [
      {
        id: 11, file_url: "a.pdf", file_type: "PDF", trade_date: "2026-08-10",
        counterparty_name: "供应商甲", amount: "800.00", abstract: "货款",
        direction: "付", needs_review: false, quality_issues: null,
        category: "purchase", category_source: "manual", invoice_requirement: "fetch",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-10T10:00:00",
      },
      {
        id: 12, file_url: "b.pdf", file_type: "PDF", trade_date: "2026-08-07",
        counterparty_name: "国家金库陕西省西咸新区支库", amount: "1116.00", abstract: "税款",
        direction: "付", needs_review: false, quality_issues: null,
        category: "tax", category_source: "rule", invoice_requirement: "none",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-07T10:00:00",
      },
      {
        id: 13, file_url: "c.pdf", file_type: "PDF", trade_date: "2026-08-06",
        counterparty_name: "客户甲有限公司", amount: "5000.00", abstract: "货款",
        direction: "收", needs_review: false, quality_issues: null,
        category: "sales_collection", category_source: "rule", invoice_requirement: "issue",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-06T10:00:00",
      },
    ];
    // 改性质后的第二次拉取（refresh）：13 号已改为 social（人工）
    const refreshed: ReceiptOut[] = rows.map((r) =>
      r.id === 13
        ? { ...r, category: "social", category_source: "manual", invoice_requirement: "none" }
        : r,
    );
    vi.mocked(api.listReceipts)
      .mockResolvedValueOnce(rows)
      .mockResolvedValueOnce(refreshed)
      .mockResolvedValue(refreshed);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(0);

    const wrapper = await mountWithAntd(true); // 真渲染抽屉（内容 teleport 到 document.body）
    await flushPromises();
    const details = wrapper.findAll("a").filter((a) => a.text() === "详情");
    expect(details).toHaveLength(3);

    await details[0].trigger("click");
    await flushPromises();
    expect(document.body.textContent).toContain("（人工）");
    expect(document.body.textContent).not.toContain("（规则）");

    await details[1].trigger("click");
    await flushPromises();
    expect(document.body.textContent).toContain("（规则）");
    expect(document.body.textContent).toContain("该性质无需发票");

    // issue（客户回款）不能说成「无需发票」：提示按 requirement 分档
    await details[2].trigger("click");
    await flushPromises();
    expect(document.body.textContent).toContain("客户回款：我方需开具销项发票");

    // 抽屉状态与列表同口径：必须查抽屉自己的 DOM（body 全域会被列表自身的标签满足 → 假绿）
    const drawerText = document.querySelector(".ant-drawer-body")?.textContent ?? "";
    expect(drawerText).toContain("待开票");
    expect(drawerText).not.toContain("无票"); // 旧 STATUS_META 对 unmatched 一律「无票」

    // 改性质 → 调接口（回单 id、选项值）+ 抽屉 refresh 事件真接线到列表 load()
    const select = wrapper.findComponent({ name: "ASelect" });
    expect(select.exists()).toBe(true);
    // 9 个业务性质 + 「跟随规则」（后端 auto 清除人工覆盖）——人工误点后的回退入口
    const options = select.props("options") as Array<{ value: string }>;
    expect(options.map((o) => o.value)).toContain("auto");
    select.vm.$emit("change", "social");
    await flushPromises();
    expect(vi.mocked(api.setReceiptCategory)).toHaveBeenCalledWith(13, "social");
    expect(vi.mocked(api.listReceipts)).toHaveBeenCalledTimes(2); // 挂载 1 次 + refresh 1 次

    // 刷新后抽屉必须显示新性质（终审 2）：detailRecord 持旧行则仍是「客户回款」旧值
    const afterRefresh = document.querySelector(".ant-drawer-body")?.textContent ?? "";
    expect(afterRefresh).toContain("社保/公积金");
    expect(afterRefresh).toContain("该性质无需发票");
    expect(afterRefresh).not.toContain("客户回款：我方需开具销项发票");

    // 该行已不在本视图（被筛选剔除 / 换周期）：抽屉应清空而非停在上一次的陈旧值（终审回复 Minor-1）
    vi.mocked(api.listReceipts).mockResolvedValue(rows.filter((r) => r.id !== 13));
    wrapper.findComponent({ name: "ReceiptDetailDrawer" }).vm.$emit("refresh");
    await flushPromises();
    const afterDropped = document.querySelector(".ant-drawer-body")?.textContent ?? "";
    expect(afterDropped).not.toContain("社保/公积金");
    expect(afterDropped).not.toContain("客户回款");
  }, 20000);

  it("状态列以 paired_invoice_id 为门：status 滞后为 paired 时不得显示「已配对」（终审 1b）", async () => {
    const api = await import("../../api/receipts");
    vi.mocked(api.listReceipts).mockResolvedValue([
      {
        // 删票后 FK SET NULL 的漏网行：paired_invoice_id 空却在催票队列
        id: 31, file_url: "a.pdf", file_type: "PDF", trade_date: "2026-08-10",
        counterparty_name: "供应商甲", amount: "800.00", abstract: "货款",
        direction: "付", needs_review: false, quality_issues: null,
        category: "purchase", category_source: "rule", invoice_requirement: "fetch",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "paired", created_at: "2026-08-10T10:00:00",
      },
      {
        id: 32, file_url: "b.pdf", file_type: "PDF", trade_date: "2026-08-09",
        counterparty_name: "供应商乙", amount: "900.00", abstract: "货款",
        direction: "付", needs_review: false, quality_issues: null,
        category: "purchase", category_source: "rule", invoice_requirement: "fetch",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: 7,
        status: "paired", created_at: "2026-08-09T10:00:00",
      },
    ]);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(0);

    const wrapper = await mountWithAntd();
    await flushPromises();
    const tags = wrapper.findAll(".ant-tag").map((t) => t.text());
    expect(tags[0]).not.toBe("已配对"); // 门不同源的老症状：绿标签与队列自相矛盾
    expect(tags[1]).toBe("已配对");
  }, 20000);

  it("其他周期也没有数据 → 不显示误导性提示", async () => {
    const api = await import("../../api/receipts");
    vi.mocked(api.listReceipts).mockResolvedValue([]);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(0);

    const wrapper = await mountWithAntd();
    await flushPromises();
    expect(wrapper.text()).not.toContain("其他周期有");
    expect(wrapper.text()).toContain("当前筛选无回单");
  }, 20000);

  it("删除按钮仅 manager/admin 可见，popconfirm 确认后调接口并刷新", async () => {
    const { useAuthStore } = await import("../../stores/auth");
    const api = await import("../../api/receipts");
    vi.mocked(api.listReceipts).mockResolvedValue([
      {
        id: 7, file_url: "f.pdf", file_type: "PDF", trade_date: "2026-08-06",
        counterparty_name: "中国建设银行", amount: "15.00", abstract: "手续费",
        direction: "付", needs_review: false, quality_issues: null,
        category: "bank_fee", category_source: "rule", invoice_requirement: "none",
        bank_code: "ccb", page_no: 1, anchor: null, paired_invoice_id: null,
        status: "unmatched", created_at: "2026-08-06T10:00:00",
      },
    ]);
    vi.mocked(api.probeReceiptsPeriod).mockResolvedValue(0);

    // 未登录态（role null）：不出删除入口
    const guest = await mountWithAntd();
    await flushPromises();
    expect(guest.findAll("a").filter((a) => a.text() === "删除")).toHaveLength(0);
    guest.unmount();
    document.body.innerHTML = "";

    // manager：出删除入口 → popconfirm 确认 → 调接口 + 刷新列表
    const auth = useAuthStore();
    auth.user = {
      id: 1, username: "m", role: "finance_manager", status: "active",
      must_change_password: false, created_at: "2026-01-01T00:00:00",
    };
    const wrapper = await mountWithAntd();
    await flushPromises();
    const del = wrapper.findAll("a").filter((a) => a.text() === "删除");
    expect(del).toHaveLength(1);
    await del[0].trigger("click");
    await flushPromises();
    await new Promise((r) => setTimeout(r, 50)); // 弹层经 transition 挂载，需一个 settle tick
    // popconfirm 确认键 teleport 到 body（antd 两字按钮插空格 → 归一化匹配）
    const ok = [...document.querySelectorAll(".ant-popconfirm .ant-btn-primary")].find(
      (b) => b.textContent?.replace(/\s/g, "") === "删除",
    );
    expect(ok).toBeTruthy();
    (ok as HTMLElement).click();
    await flushPromises();
    expect(vi.mocked(api.deleteReceipt)).toHaveBeenCalledWith(7);
    expect(vi.mocked(api.listReceipts)).toHaveBeenCalledTimes(3); // guest 挂载 1 + manager 挂载 1 + 删除后刷新 1
  }, 20000);
});
