// 路由表与全局守卫：无 token 必跳 login，adminOnly 路由非 admin 必挡
import { createRouter, createWebHistory } from "vue-router";
import { TOKEN_KEY } from "../api/client";
import { useAuthStore } from "../stores/auth";

const routes = [
  { path: "/login", component: () => import("../views/LoginView.vue") },
  { path: "/", component: () => import("../views/DashboardView.vue") },
  { path: "/invoices", component: () => import("../views/InvoiceListView.vue") },
  { path: "/audit", component: () => import("../views/AuditView.vue"), meta: { adminOnly: true } },
  { path: "/settings", component: () => import("../views/SettingsView.vue"), meta: { adminOnly: true } },
];

const router = createRouter({ history: createWebHistory(), routes });

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (to.path !== "/login" && !auth.token) {
    return { path: "/login", query: { redirect: to.fullPath } };
  }
  if (auth.token && !auth.user) {
    try {
      await auth.loadMe();
    } catch {
      auth.token = null;
      localStorage.removeItem(TOKEN_KEY);
      return { path: "/login" };
    }
  }
  if (to.meta.adminOnly && !auth.isAdmin) {
    return { path: "/" };
  }
  return true;
});

export default router;
