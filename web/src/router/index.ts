// 路由表与全局守卫：无 token 必跳 login，adminOnly 路由非 admin 必挡
import { createRouter, createWebHistory } from "vue-router";
import { TOKEN_KEY } from "../api/client";
import { useAuthStore } from "../stores/auth";

const routes = [
  { path: "/login", component: () => import("../views/LoginView.vue") },
  { path: "/", component: () => import("../views/DashboardView.vue"), meta: { title: "工作台" } },
  { path: "/invoices", component: () => import("../views/InvoiceListView.vue"), meta: { title: "发票列表" } },
  { path: "/receipts", component: () => import("../views/ReceiptsView.vue"), meta: { title: "银行回单" } },
  { path: "/tasks", component: () => import("../views/AsyncTasksView.vue"), meta: { title: "异步任务", financeOnly: true } },
  // 报销：员工与财务共用（员工看本人，财务看全部并审批）
  { path: "/expenses", component: () => import("../views/ExpensesView.vue"), meta: { title: "我的报销" } },
  // 修改密码：顶栏自愿改密 + 管理员重置后的强制改密（同一页）
  { path: "/change-password", component: () => import("../views/ChangePasswordView.vue"), meta: { title: "修改密码" } },
  // 我的令牌：自助签发（所有角色可见——平台侧按用户配令牌是主路径）
  { path: "/mcp-tokens", component: () => import("../views/McpTokensView.vue"), meta: { title: "我的令牌" } },
  { path: "/audit", component: () => import("../views/AuditView.vue"), meta: { title: "审计日志", adminOnly: true } },
  { path: "/ops", component: () => import("../views/OpsView.vue"), meta: { title: "运维", adminOnly: true } },
  { path: "/settings", component: () => import("../views/SettingsView.vue"), meta: { title: "系统配置", adminOnly: true } },
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
  // 强制改密：管理员重置后只能先改密（后端默认全拦，前端把人送到改密页）
  if (auth.user?.must_change_password && to.path !== "/change-password") {
    return { path: "/change-password" };
  }
  if (to.meta.adminOnly && !auth.isAdmin) {
    return { path: "/" };
  }
  if (
    to.meta.financeOnly &&
    !["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "")
  ) {
    return { path: "/" };
  }
  return true;
});

export default router;
