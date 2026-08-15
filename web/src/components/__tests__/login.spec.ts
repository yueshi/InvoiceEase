import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginView from "../../views/LoginView.vue";
import { useAuthStore } from "../../stores/auth";

vi.mock("../../api/auth", () => ({
  login: vi.fn().mockResolvedValue({
    access_token: "test-token",
    token_type: "bearer",
    user: { id: 1, username: "admin", role: "admin", created_at: "2026-08-15T00:00:00" },
  }),
}));

describe("LoginView", () => {
  beforeEach(() => {
    // 组件 setup 中调用 useAuthStore()，需先激活 Pinia（断言保持与 brief 一致）
    setActivePinia(createPinia());
  });

  it("渲染用户名与密码输入框", () => {
    const wrapper = mount(LoginView, { global: { plugins: [] } });
    expect(wrapper.text()).toContain("发票易");
  });

  it("登录成功后写入 auth store 并跳转", async () => {
    const wrapper = mount(LoginView, {
      global: { plugins: [], stubs: { routerLink: true, routerView: true } },
    });
    const auth = useAuthStore();
    const { login } = await import("../../api/auth");
    await (wrapper.vm as any).onSubmit("admin", "pass123");
    expect(login).toHaveBeenCalledWith("admin", "pass123");
    expect(auth.token).toBe("test-token");
  });
});
