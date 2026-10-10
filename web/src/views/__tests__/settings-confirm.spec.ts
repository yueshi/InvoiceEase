// src/views/__tests__/settings-confirm.spec.ts
// P0-2 / v1.1 §7.5.1：银行账号删除走 ConfirmModal（high 风险，需填理由）
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import SettingsView from "../SettingsView.vue";

const mocks = vi.hoisted(() => ({
  listBankAccounts: vi.fn(),
  deleteBankAccount: vi.fn(),
  createBankAccount: vi.fn(),
  updateBankAccount: vi.fn(),
}));

vi.mock("../../api/bankAccounts", () => mocks);
vi.mock("../../api/mailboxes", () => ({
  listMailboxes: vi.fn().mockResolvedValue([]),
  createMailbox: vi.fn(), updateMailbox: vi.fn(),
  testMailbox: vi.fn(), pollMailbox: vi.fn(),
}));
vi.mock("../../api/users", () => ({
  listUsers: vi.fn().mockResolvedValue([]),
  createUser: vi.fn(), updateUser: vi.fn(),
}));
vi.mock("../../api/companyInfos", () => ({
  listCompanyInfos: vi.fn().mockResolvedValue([]),
  createCompanyInfo: vi.fn(), updateCompanyInfo: vi.fn(), deleteCompanyInfo: vi.fn(),
}));

/** 让 a-table stub 渲染每行的 actions 单元格（真实 antd 未在测试环境注册）。 */
const tableStub = {
  props: ["dataSource"],
  template: `<div><div v-for="row in dataSource" :key="row.id" class="row">
    <slot name="bodyCell" :column="{ key: 'actions' }" :record="row" />
  </div></div>`,
};

function mountView() {
  return mount(SettingsView, {
    global: {
      stubs: {
        "a-tabs": { template: "<div><slot /></div>" },
        "a-tab-pane": { template: "<div><slot /></div>" },
        "a-modal": { template: "<div><slot /></div>" },
        "a-table": tableStub,
      },
    },
  });
}

describe("SettingsView × ConfirmModal（v1.1 §7.5.1）", () => {
  it("删除银行账号：先弹 high 风险确认，填理由并确认后才调 API", async () => {
    mocks.listBankAccounts.mockResolvedValue([
      { id: 9, account_no: "6222021234567890", account_name: "本司基本户",
        bank_name: "工商银行", bank_code: null, remark: null,
        is_default: true, enabled: true },
    ]);
    mocks.deleteBankAccount.mockResolvedValue({ ok: true });

    const w = mountView();
    await flushPromises();

    // 点「删除」→ 弹确认框，未调用 API
    const del = w.findAll("a").find((a) => a.text() === "删除");
    expect(del).toBeTruthy();
    await del!.trigger("click");
    expect(w.find(".cm-mask").exists()).toBe(true);
    expect(w.find(".cm-title").text()).toContain("删除银行账号");
    expect(mocks.deleteBankAccount).not.toHaveBeenCalled();

    // high 风险：未填理由不可确认
    expect(w.find("button.cm-confirm").attributes("disabled")).toBeDefined();
    await w.find("textarea.cm-reason").setValue("账户已销户");
    await w.find("button.cm-confirm").trigger("click");
    await flushPromises();

    expect(mocks.deleteBankAccount).toHaveBeenCalledWith(9);
  });

  it("取消确认：不调 API", async () => {
    mocks.listBankAccounts.mockResolvedValue([
      { id: 10, account_no: "6222021234567891", account_name: "备用户",
        bank_name: null, bank_code: null, remark: null,
        is_default: false, enabled: true },
    ]);
    mocks.deleteBankAccount.mockClear();

    const w = mountView();
    await flushPromises();

    const del = w.findAll("a").find((a) => a.text() === "删除");
    await del!.trigger("click");
    await w.find("button.cm-cancel").trigger("click");
    await flushPromises();

    expect(w.find(".cm-mask").exists()).toBe(false);
    expect(mocks.deleteBankAccount).not.toHaveBeenCalled();
  });
});
