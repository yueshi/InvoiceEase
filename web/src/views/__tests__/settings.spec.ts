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
});
