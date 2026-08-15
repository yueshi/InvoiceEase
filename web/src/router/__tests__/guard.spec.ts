import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useAuthStore } from "../../stores/auth";

describe("路由守卫逻辑", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it("无 token 访问受保护路由 → 重定向 /login", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = null;
    const result = await router.push("/invoices");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("非 admin 访问 /audit → 重定向首页", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = "t";
    auth.user = { id: 1, username: "u", role: "finance_staff", created_at: "" };
    const result = await router.push("/audit");
    expect(router.currentRoute.value.path).toBe("/");
  });
});
