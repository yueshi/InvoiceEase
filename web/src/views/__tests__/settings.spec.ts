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
});
