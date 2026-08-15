import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../auth";

vi.mock("../../api/auth", () => ({
  login: vi.fn().mockResolvedValue({
    access_token: "t",
    token_type: "bearer",
    user: { id: 1, username: "u", role: "finance_staff", created_at: "2026-08-15T00:00:00" },
  }),
  logout: vi.fn().mockResolvedValue({ ok: true }),
  fetchMe: vi.fn().mockResolvedValue({
    id: 1, username: "u", role: "finance_staff", created_at: "2026-08-15T00:00:00",
  }),
}));

describe("auth store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it("login 持久化 token 到 localStorage", async () => {
    const auth = useAuthStore();
    await auth.login("u", "p");
    expect(auth.token).toBe("t");
    expect(localStorage.getItem("invoicing_token")).toBe("t");
  });

  it("isAdmin 按角色判定", async () => {
    const auth = useAuthStore();
    await auth.login("u", "p");
    expect(auth.isAdmin).toBe(false);
  });

  it("logout 清空状态", async () => {
    const auth = useAuthStore();
    await auth.login("u", "p");
    await auth.logout();
    expect(auth.token).toBeNull();
    expect(localStorage.getItem("invoicing_token")).toBeNull();
  });
});
