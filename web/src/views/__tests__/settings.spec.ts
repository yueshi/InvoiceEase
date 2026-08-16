// src/views/__tests__/settings.spec.ts
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import SettingsView from "../SettingsView.vue";

vi.mock("../../api/mailboxes", () => ({
  listMailboxes: vi.fn().mockResolvedValue([]),
  createMailbox: vi.fn(),
  updateMailbox: vi.fn(),
  testMailbox: vi.fn(),
  pollMailbox: vi.fn(),
}));
vi.mock("../../api/users", () => ({
  listUsers: vi.fn().mockResolvedValue([]),
  createUser: vi.fn(),
  updateUser: vi.fn(),
}));
vi.mock("../../api/companyInfos", () => ({
  listCompanyInfos: vi.fn().mockResolvedValue([]),
  createCompanyInfo: vi.fn(),
  updateCompanyInfo: vi.fn(),
  deleteCompanyInfo: vi.fn(),
}));

describe("SettingsView", () => {
  it("渲染邮箱与用户两个 tab", () => {
    const wrapper = mount(SettingsView);
    expect(wrapper.exists()).toBe(true);
  });

  it("邮箱表单渲染类型切换选项", () => {
    const wrapper = mount(SettingsView, { global: { stubs: { "a-tabs": { template: "<div><slot /></div>" }, "a-tab-pane": { template: "<div><slot /></div>" }, "a-modal": { template: "<div><slot /></div>" }, "a-table": { template: "<div />" } } } });
    // 类型切换选项渲染（默认 IMAP：Agently 专属字段默认隐藏）
    expect(wrapper.find("a-radio-button[value='imap']").text()).toContain("IMAP");
    expect(wrapper.find("a-radio-button[value='agently']").text()).toContain("Agently");
    expect(wrapper.find("a-form-item[label='类型']").exists()).toBe(true);
    expect(wrapper.find("a-form-item[label='IMAP 主机']").exists()).toBe(true);
    expect(wrapper.find("a-form-item[label='工作区']").exists()).toBe(false);
    expect(wrapper.find("a-form-item[label='Access Token']").exists()).toBe(false);
  });

  it("渲染常用税号/公司 tab 与公司表单", () => {
    const wrapper = mount(SettingsView, { global: { stubs: { "a-tabs": { template: "<div><slot /></div>" }, "a-tab-pane": { template: "<div><slot /></div>" }, "a-modal": { template: "<div><slot /></div>" }, "a-table": { template: "<div />" } } } });
    // 新建公司按钮（区别于邮箱/用户；测试环境未注册 antd，a-button 为原始自定义元素）
    expect(wrapper.findAll("a-button").some((b) => b.text().includes("新建公司"))).toBe(true);
    // 公司表单字段渲染（名称/税号/类型/默认/备注）
    expect(wrapper.find("a-form-item[label='公司名称']").exists()).toBe(true);
    expect(wrapper.find("a-form-item[label='税号']").exists()).toBe(true);
    expect(wrapper.find("a-form-item[label='类型']").exists()).toBe(true);
    expect(wrapper.find("a-form-item[label='默认']").exists()).toBe(true);
    expect(wrapper.find("a-form-item[label='备注']").exists()).toBe(true);
  });
});
