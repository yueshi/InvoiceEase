import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
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

  it("强制改密时访问任何页面 → 重定向 /change-password", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = "t";
    auth.user = {
      id: 1, username: "u", role: "employee", created_at: "",
      status: "active", must_change_password: true,
    };
    await router.push("/invoices");
    expect(router.currentRoute.value.path).toBe("/change-password");

    // 改密页本身可达，否则用户无法自救
    await router.push("/change-password");
    expect(router.currentRoute.value.path).toBe("/change-password");
  });

  it("非 admin 访问 /audit → 重定向首页", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = "t";
    auth.user = {
      id: 1, username: "u", role: "finance_staff", created_at: "",
      status: "active", must_change_password: false,
    };
    const result = await router.push("/audit");
    expect(router.currentRoute.value.path).toBe("/");
  });

  // ---- Agent 深链票据（design/2026-10-03） ----

  it("带 ticket 无会话 → 兑换成功后进目标页且剥票", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = null;
    vi.spyOn(auth, "ticketLogin").mockImplementation(async () => {
      auth.token = "t";
      auth.user = {
        id: 1, username: "u", role: "finance_staff", created_at: "",
        status: "active", must_change_password: false,
      };
    });
    await router.push("/invoices?ticket=T1&status=pending_review");
    expect(router.currentRoute.value.path).toBe("/invoices");
    expect(router.currentRoute.value.query.ticket).toBeUndefined();
    expect(router.currentRoute.value.query.status).toBe("pending_review");
  });

  it("带 ticket 无会话且兑换失败 → 落登录页，redirect 保留目标、带 expired", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = null;
    vi.spyOn(auth, "ticketLogin").mockRejectedValue(new Error("401"));
    await router.push("/invoices?ticket=T1&status=pending_review");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.expired).toBe("1");
    expect(String(router.currentRoute.value.query.redirect)).toBe(
      "/invoices?status=pending_review",
    );
  });

  it("带 ticket 已有会话 → 不兑换，直接剥票进入", async () => {
    const router = (await import("../index")).default;
    const auth = useAuthStore();
    auth.token = "t";
    auth.user = {
      id: 1, username: "u", role: "finance_staff", created_at: "",
      status: "active", must_change_password: false,
    };
    const spy = vi.spyOn(auth, "ticketLogin");
    await router.push("/invoices?ticket=T1");
    expect(router.currentRoute.value.path).toBe("/invoices");
    expect(router.currentRoute.value.query.ticket).toBeUndefined();
    expect(spy).not.toHaveBeenCalled();
  });
});
