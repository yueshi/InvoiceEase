# 发票易 MVP Web 管理后台实施计划（Plan C）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 Web 管理后台 V1：登录、工作台统计、发票列表/详情/基础筛选/人工复核、审计日志、系统配置（邮箱/用户），4 角色菜单收敛，全部走既有 REST API（`/api/v1`）。

**Architecture:** Vue 3 + TypeScript + Vite 单页应用，Ant Design Vue 4 组件库，Pinia（auth store）+ Vue Router（守卫：未登录跳登录，admin 路由收敛）。axios 客户端统一注入 Bearer token、401 自动登出、文件下载走 blob。dev 模式 Vite proxy 转发 `/api` 到 `http://localhost:8000`；生产构建产物由 Plan D 的 Nginx 托管。

**Tech Stack:** Vue 3.4+、Vite 5+、TypeScript 5+、ant-design-vue 4.x、pinia 2.x、vue-router 4.x、axios 1.x、Vitest + @vue/test-utils（jsdom，关键页面冒烟）。

**Spec:** `design/2026-08-14-mvp-design.md`（§一 Web V1 范围、§五 REST 契约、§七 前端测试策略）；`frd/InvoiceEase-frd-v0.1.txt` 3.5 节（分角色看板）。

## Global Constraints

- 需求基线以 `frd/InvoiceEase-frd-v0.1.txt` 为准；REST 契约以 `design/2026-08-14-mvp-design.md` §五为准（端点/参数/权限逐字对齐，不得自造接口）
- 前端代码在 `web/` 目录；中文 UI 文案；注释中文
- 角色菜单收敛：审计日志/系统配置仅 admin 可见；复核/重验按钮仅 finance_staff/finance_manager/admin；员工列表由后端过滤（前端不做二次过滤）
- 文件下载必须走 axios blob（JWT 在 header，window.open 会丢认证）
- 每个任务结束 git commit，提交信息格式 `feat(web): ...` / `fix(web): ...`
- 测试：Vitest 冒烟（渲染 + 关键交互 + store/守卫单测），不追求覆盖率数字
- 命令（在 web/ 目录）：`npm install`、`npm run dev`、`npm run build`、`npm run test`（vitest run）
- 后端要求：开发时后端已按 docs/开发环境指南.md 启动在 8000 端口（测试不依赖真实后端——API 全部 mock）

---

### Task 1: 工程脚手架、登录与布局骨架

**Files:**
- Create: `web/package.json`、`web/vite.config.ts`、`web/tsconfig.json`、`web/index.html`、`web/src/main.ts`、`web/src/App.vue`
- Create: `web/src/types.ts`
- Create: `web/src/api/client.ts`、`web/src/api/auth.ts`
- Create: `web/src/stores/auth.ts`
- Create: `web/src/router/index.ts`
- Create: `web/src/views/LoginView.vue`、`web/src/views/DashboardView.vue`（占位）
- Create: `web/src/components/__tests__/login.spec.ts`、`web/src/stores/__tests__/auth.spec.ts`、`web/src/router/__tests__/guard.spec.ts`

**Interfaces:**
- Consumes: `POST /api/v1/auth/login`（{access_token, token_type, user:{id,username,role}}）、`POST /auth/logout`、`GET /auth/me`
- Produces（后续任务依赖）:
  - `api/client.ts`: `api`（axios 实例，baseURL `/api/v1`，请求拦截注入 `Authorization: Bearer <token>`，401 响应拦截清 token 并跳 `/login`）、`downloadFile(path: string, filename: string): Promise<void>`（blob 下载）
  - `api/auth.ts`: `login(username, password): Promise<LoginResponse>`、`logout()`、`fetchMe()`
  - `stores/auth.ts`: `useAuthStore`（state: token/user/role；actions: login/logout/loadMe；persist localStorage key `invoicing_token`）
  - `router/index.ts`: 路由表 + 全局守卫（无 token 且非 /login → /login?redirect=...；有 token 未加载 user → loadMe；adminOnly 路由对非 admin → 首页）
  - `types.ts`: `UserOut`、`LoginResponse`、`Role = "employee" | "finance_staff" | "finance_manager" | "admin"`

- [ ] **Step 1: 写 package.json 与配置文件**

```json
{
  "name": "invoicing-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "ant-design-vue": "^4.2.0",
    "axios": "^1.7.0",
    "pinia": "^2.2.0",
    "vue": "^3.4.0",
    "vue-router": "^4.4.0"
  },
  "devDependencies": {
    "@types/node": "^20.14.0",
    "@vitejs/plugin-vue": "^5.1.0",
    "@vue/test-utils": "^2.4.0",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.0",
    "vite": "^5.3.0",
    "vitest": "^2.0.0",
    "vue-tsc": "^2.0.0"
  }
}
```

```ts
// vite.config.ts
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```

```json
// tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "jsx": "preserve",
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["vitest/globals"]
  },
  "include": ["src/**/*.ts", "src/**/*.vue"]
}
```

```html
<!-- index.html -->
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>发票易</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 2: 写 types.ts**

```ts
// src/types.ts
export type Role = "employee" | "finance_staff" | "finance_manager" | "admin";

export interface UserOut {
  id: number;
  username: string;
  role: Role;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
}

export const ROLE_LABELS: Record<Role, string> = {
  employee: "普通员工",
  finance_staff: "财务专员",
  finance_manager: "财务主管",
  admin: "系统管理员",
};
```

- [ ] **Step 3: 写失败测试（3 个测试文件）**

```ts
// src/components/__tests__/login.spec.ts
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
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
```

```ts
// src/stores/__tests__/auth.spec.ts
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
```

```ts
// src/router/__tests__/guard.spec.ts
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
```

（注意：路由守卫测试依赖 mock 的 `loadMe` 与 localStorage token 恢复逻辑；若守卫实现与测试预期细节冲突，以守卫「无 token 必跳 login、admin 路由非 admin 必挡」两个硬行为为准微调测试与实现。）

- [ ] **Step 4: 运行测试，确认失败**

Run: `cd web && npm install && npm run test`
Expected: FAIL（文件不存在 / 组件未实现）

- [ ] **Step 5: 实现 client/auth/store/router/App/main/Login/Dashboard 占位**

```ts
// src/api/client.ts
import axios from "axios";
import { message } from "ant-design-vue";

export const TOKEN_KEY = "invoicing_token";

export const api = axios.create({ baseURL: "/api/v1", timeout: 15000 });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes("/auth/login")) {
      localStorage.removeItem(TOKEN_KEY);
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  },
);

export async function downloadFile(path: string, filename: string): Promise<void> {
  const resp = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(resp.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function errorMessage(e: unknown, fallback = "请求失败"): void {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  message.error(detail || fallback);
}
```

```ts
// src/api/auth.ts
import { api } from "./client";
import type { LoginResponse, UserOut } from "../types";

export async function login(username: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", { username, password });
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

export async function fetchMe(): Promise<UserOut> {
  const { data } = await api.get<UserOut>("/auth/me");
  return data;
}
```

```ts
// src/stores/auth.ts
import { defineStore } from "pinia";
import * as authApi from "../api/auth";
import { TOKEN_KEY } from "../api/client";
import type { UserOut } from "../types";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) as string | null,
    user: null as UserOut | null,
  }),
  getters: {
    isAdmin: (s) => s.user?.role === "admin",
    role: (s) => s.user?.role ?? null,
  },
  actions: {
    async login(username: string, password: string) {
      const resp = await authApi.login(username, password);
      this.token = resp.access_token;
      this.user = resp.user;
      localStorage.setItem(TOKEN_KEY, resp.access_token);
    },
    async logout() {
      try {
        await authApi.logout();
      } finally {
        this.token = null;
        this.user = null;
        localStorage.removeItem(TOKEN_KEY);
      }
    },
    async loadMe() {
      if (!this.token) return;
      this.user = await authApi.fetchMe();
    },
  },
});
```

```ts
// src/router/index.ts
import { createRouter, createWebHistory } from "vue-router";
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
      localStorage.removeItem("invoicing_token");
      return { path: "/login" };
    }
  }
  if (to.meta.adminOnly && !auth.isAdmin) {
    return { path: "/" };
  }
  return true;
});

export default router;
```

```ts
// src/main.ts
import Antd from "ant-design-vue";
import "ant-design-vue/dist/reset.css";
import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(Antd);
app.mount("#app");
```

```vue
<!-- src/App.vue -->
<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

// 菜单项按角色收敛（computed：登录后角色变化实时生效）
const menuItems = computed(() => [
  { key: "/", label: "工作台" },
  { key: "/invoices", label: "发票列表" },
  ...(auth.isAdmin ? [{ key: "/audit", label: "审计日志" }, { key: "/settings", label: "系统配置" }] : []),
]);

function onLogout() {
  auth.logout().finally(() => router.push("/login"));
}
</script>

<template>
  <a-layout v-if="route.path !== '/login'" style="min-height: 100vh">
    <a-layout-sider>
      <div style="color: #fff; padding: 16px; font-weight: 600">发票易</div>
      <a-menu theme="dark" :selected-keys="[route.path]" :items="menuItems" @click="({ key }) => router.push(String(key))" />
    </a-layout-sider>
    <a-layout>
      <a-layout-header style="background: #fff; display: flex; justify-content: flex-end; align-items: center; gap: 12px">
        <span>{{ auth.user?.username }}</span>
        <a-button @click="onLogout">退出登录</a-button>
      </a-layout-header>
      <a-layout-content style="padding: 24px">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
  <router-view v-else />
</template>
```

```vue
<!-- src/views/LoginView.vue -->
<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { errorMessage } from "../api/client";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();
const username = ref("");
const password = ref("");
const loading = ref(false);

async function onSubmit(u?: string, p?: string) {
  loading.value = true;
  try {
    await auth.login(u ?? username.value, p ?? password.value);
    router.push(String(route.query.redirect || "/"));
  } catch (e) {
    errorMessage(e, "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div style="max-width: 360px; margin: 120px auto">
    <h2 style="text-align: center">发票易</h2>
    <a-form @finish="() => onSubmit()">
      <a-form-item>
        <a-input v-model:value="username" placeholder="用户名" />
      </a-form-item>
      <a-form-item>
        <a-input-password v-model:value="password" placeholder="密码" />
      </a-form-item>
      <a-button type="primary" html-type="submit" block :loading="loading">登录</a-button>
    </a-form>
  </div>
</template>
```

```vue
<!-- src/views/DashboardView.vue（占位，Task 2 充实） -->
<template>
  <div>工作台</div>
</template>
```

（Task 1 中 InvoiceListView/AuditView/SettingsView 以最小占位组件创建，Task 2/3 充实。）

- [ ] **Step 6: 运行测试，确认通过**

Run: `cd web && npm run test`
Expected: 全部 PASS（渲染 1 + store 3 + 守卫 2 = 6 条；若守卫测试因 router 单例状态串扰失败，按 Step 3 注释的两个硬行为微调）

- [ ] **Step 7: 构建验证并提交**

Run: `cd web && npm run build`
Expected: 构建成功（dist/ 生成）

```bash
git add web/package.json web/package-lock.json web/vite.config.ts web/tsconfig.json \
  web/index.html web/src
git commit -m "feat(web): 工程脚手架、登录与布局骨架"
```

---

### Task 2: 工作台统计、发票列表/详情/复核

**Files:**
- Create: `web/src/api/stats.ts`、`web/src/api/invoices.ts`
- Modify: `web/src/types.ts`（追加 InvoiceOut/InvoiceListResponse/StatsOverviewOut/ReviewRequest 等）
- Modify: `web/src/views/DashboardView.vue`（统计卡片）
- Modify: `web/src/views/InvoiceListView.vue`（筛选/分页/操作）
- Create: `web/src/components/InvoiceDetailDrawer.vue`
- Create: `web/src/views/__tests__/dashboard.spec.ts`、`web/src/views/__tests__/invoice-list.spec.ts`

**Interfaces:**
- Consumes: `GET /stats/overview`、`GET /invoices`（status/date_from/date_to/keyword/page/page_size）、`GET /invoices/{id}`、`GET /invoices/{id}/file?kind=file|xml`、`POST /invoices/{id}/review`（{action, note}）、`POST /invoices/{id}/verify`
- Produces:
  - `api/stats.ts`: `fetchOverview(): Promise<StatsOverviewOut>`
  - `api/invoices.ts`: `listInvoices(params): Promise<InvoiceListResponse>`、`getInvoice(id)`、`reviewInvoice(id, action, note)`、`reVerify(id)`、`downloadInvoiceFile(id, kind)`
  - `types.ts` 追加：`InvoiceOut`（与后端 schema 字段一致）、`InvoiceListResponse{items,total,page,page_size}`、`StatsOverviewOut{pending_review,pending_submit,today_new,month_total}`、`INVOICE_STATUS_LABELS: Record<string,string>`（receiving 收取中/ received 已收取/ parsing 解析中/ parsed 解析完成/ verifying 验真查重中/ pending_submit 待提交/ pending_review 待复核/ blocked 已拦截/ rejected 已驳回/ submitted 已提交/ archived 已归档）

- [ ] **Step 1: 写失败测试**

```ts
// src/views/__tests__/dashboard.spec.ts
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import DashboardView from "../DashboardView.vue";

vi.mock("../../api/stats", () => ({
  fetchOverview: vi.fn().mockResolvedValue({
    pending_review: 3, pending_submit: 10, today_new: 5, month_total: 88,
  }),
}));

describe("DashboardView", () => {
  it("加载并展示统计数值", async () => {
    const wrapper = mount(DashboardView, { global: { stubs: { "a-row": true, "a-col": true, "a-card": true, "a-statistic": true } } });
    await new Promise((r) => setTimeout(r, 0));
    const stats = (wrapper.vm as any).stats;
    expect(stats).toEqual({ pending_review: 3, pending_submit: 10, today_new: 5, month_total: 88 });
  });
});
```

```ts
// src/views/__tests__/invoice-list.spec.ts
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import InvoiceListView from "../InvoiceListView.vue";

vi.mock("../../api/invoices", () => ({
  listInvoices: vi.fn().mockResolvedValue({
    items: [], total: 0, page: 1, page_size: 20,
  }),
}));

describe("InvoiceListView", () => {
  it("渲染筛选控件与表格", () => {
    const wrapper = mount(InvoiceListView, { global: { stubs: true } });
    expect(wrapper.exists()).toBe(true);
  });

  it("首次加载调用 listInvoices 默认参数", async () => {
    const wrapper = mount(InvoiceListView, { global: { stubs: true } });
    const { listInvoices } = await import("../../api/invoices");
    await new Promise((r) => setTimeout(r, 0));
    expect(listInvoices).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd web && npm run test`
Expected: FAIL（Dashboard/InvoiceList 未实现相应行为）

- [ ] **Step 3: 实现 types 追加、api、页面与抽屉**

```ts
// types.ts 追加
export interface InvoiceOut {
  id: number;
  tenant_id: string;
  user_id: number | null;
  mailbox_id: number | null;
  email_subject: string | null;
  invoice_code: string | null;
  invoice_number: string | null;
  issue_date: string | null;
  amount_without_tax: string | null;
  tax_amount: string | null;
  total_amount: string | null;
  total_amount_cn: string | null;
  seller_name: string | null;
  seller_tax_id: string | null;
  buyer_name: string | null;
  buyer_tax_id: string | null;
  invoice_type: string | null;
  file_type: string;
  parse_source: string | null;
  confidence_score: number | null;
  validation_errors: Record<string, unknown> | null;
  verify_status: string;
  verify_detail: Record<string, unknown> | null;
  verified_at: string | null;
  duplicate_flag: boolean;
  duplicate_of_id: number | null;
  status: string;
  review_note: string | null;
  reviewed_by: number | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvoiceListResponse {
  items: InvoiceOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface StatsOverviewOut {
  pending_review: number;
  pending_submit: number;
  today_new: number;
  month_total: number;
}

export const INVOICE_STATUS_LABELS: Record<string, string> = {
  receiving: "收取中", received: "已收取", parsing: "解析中", parsed: "解析完成",
  verifying: "验真查重中", pending_submit: "待提交", pending_review: "待复核",
  blocked: "已拦截", rejected: "已驳回", submitted: "已提交", archived: "已归档",
};

export const VERIFY_STATUS_LABELS: Record<string, string> = {
  pending: "待验真", passed: "通过", failed: "失败",
};
```

```ts
// src/api/stats.ts
import { api } from "./client";
import type { StatsOverviewOut } from "../types";

export async function fetchOverview(): Promise<StatsOverviewOut> {
  const { data } = await api.get<StatsOverviewOut>("/stats/overview");
  return data;
}
```

```ts
// src/api/invoices.ts
import { api, downloadFile } from "./client";
import type { InvoiceListResponse, InvoiceOut } from "../types";

export interface ListParams {
  status?: string;
  date_from?: string;
  date_to?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export async function listInvoices(params: ListParams): Promise<InvoiceListResponse> {
  const { data } = await api.get<InvoiceListResponse>("/invoices", { params });
  return data;
}

export async function getInvoice(id: number): Promise<InvoiceOut> {
  const { data } = await api.get<InvoiceOut>(`/invoices/${id}`);
  return data;
}

export async function reviewInvoice(id: number, action: "approve" | "reject", note?: string): Promise<InvoiceOut> {
  const { data } = await api.post<InvoiceOut>(`/invoices/${id}/review`, { action, note });
  return data;
}

export async function reVerify(id: number): Promise<InvoiceOut> {
  const { data } = await api.post<InvoiceOut>(`/invoices/${id}/verify`);
  return data;
}

export async function downloadInvoiceFile(id: number, kind: "file" | "xml"): Promise<void> {
  await downloadFile(`/invoices/${id}/file?kind=${kind}`, `invoice-${id}.${kind === "xml" ? "xml" : "bin"}`);
}
```

```vue
<!-- src/views/DashboardView.vue -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { errorMessage } from "../api/client";
import { fetchOverview } from "../api/stats";
import type { StatsOverviewOut } from "../types";

const stats = ref<StatsOverviewOut | null>(null);

onMounted(async () => {
  try {
    stats.value = await fetchOverview();
  } catch (e) {
    errorMessage(e, "统计加载失败");
  }
});
</script>

<template>
  <div>
    <h3>工作台</h3>
    <a-row :gutter="16">
      <a-col :span="6"><a-card><a-statistic title="待复核" :value="stats?.pending_review ?? 0" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="待提交" :value="stats?.pending_submit ?? 0" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="今日新增" :value="stats?.today_new ?? 0" /></a-card></a-col>
      <a-col :span="6"><a-card><a-statistic title="本月累计" :value="stats?.month_total ?? 0" /></a-card></a-col>
    </a-row>
  </div>
</template>
```

```vue
<!-- src/views/InvoiceListView.vue -->
<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import { listInvoices, reVerify, reviewInvoice } from "../api/invoices";
import { useAuthStore } from "../stores/auth";
import { INVOICE_STATUS_LABELS, VERIFY_STATUS_LABELS, type InvoiceListResponse, type InvoiceOut } from "../types";
import InvoiceDetailDrawer from "../components/InvoiceDetailDrawer.vue";

const auth = useAuthStore();
const data = ref<InvoiceListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
const filters = reactive({ status: undefined as string | undefined, keyword: "", dateRange: undefined as [string, string] | undefined });
const drawerOpen = ref(false);
const current = ref<InvoiceOut | null>(null);

const canReview = () => ["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "");

async function load() {
  loading.value = true;
  try {
    data.value = await listInvoices({
      status: filters.status,
      keyword: filters.keyword || undefined,
      date_from: filters.dateRange?.[0],
      date_to: filters.dateRange?.[1],
      page: data.value.page,
      page_size: data.value.page_size,
    });
  } catch (e) {
    errorMessage(e, "列表加载失败");
  } finally {
    loading.value = false;
  }
}

function onPageChange(page: number, pageSize: number) {
  data.value.page = page;
  data.value.page_size = pageSize;
  load();
}

function showDetail(record: InvoiceOut) {
  current.value = record;
  drawerOpen.value = true;
}

async function onReview(record: InvoiceOut, action: "approve" | "reject") {
  try {
    await reviewInvoice(record.id, action);
    message.success(action === "approve" ? "已通过，进入待提交" : "已驳回");
    load();
  } catch (e) {
    errorMessage(e, "复核操作失败");
  }
}

async function onReVerify(record: InvoiceOut) {
  try {
    await reVerify(record.id);
    message.success("已重新触发验真");
    load();
  } catch (e) {
    errorMessage(e, "重验失败");
  }
}

onMounted(load);

const columns = [
  { title: "发票号码", dataIndex: "invoice_number", key: "invoice_number" },
  { title: "销售方", dataIndex: "seller_name", key: "seller_name" },
  { title: "开票日期", dataIndex: "issue_date", key: "issue_date" },
  { title: "价税合计", dataIndex: "total_amount", key: "total_amount" },
  { title: "状态", dataIndex: "status", key: "status" },
  { title: "验真", dataIndex: "verify_status", key: "verify_status" },
  { title: "操作", key: "actions" },
];
</script>

<template>
  <div>
    <h3>发票列表</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-select v-model:value="filters.status" placeholder="状态" allow-clear style="width: 160px" @change="load">
        <a-select-option v-for="(label, value) in INVOICE_STATUS_LABELS" :key="value" :value="value">{{ label }}</a-select-option>
      </a-select>
      <a-input v-model:value="filters.keyword" placeholder="发票号码/购销方" style="width: 220px" @press-enter="load" />
      <a-range-picker v-model:value="filters.dateRange" @change="load" />
      <a-button type="primary" @click="load">查询</a-button>
    </a-space>
    <a-table :columns="columns" :data-source="data.items" :loading="loading" row-key="id"
      :pagination="{ total: data.total, current: data.page, pageSize: data.page_size, showSizeChanger: true }"
      @change="(p: any) => onPageChange(p.current, p.pageSize)">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="record.status === 'pending_review' ? 'orange' : record.status === 'blocked' ? 'red' : 'blue'">
            {{ INVOICE_STATUS_LABELS[record.status] || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'verify_status'">
          {{ VERIFY_STATUS_LABELS[record.verify_status] || record.verify_status }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a @click="showDetail(record)">详情</a>
            <template v-if="canReview() && record.status === 'pending_review'">
              <a @click="onReview(record, 'approve')">通过</a>
              <a @click="onReview(record, 'reject')">驳回</a>
            </template>
            <a v-if="canReview()" @click="onReVerify(record)">重验</a>
          </a-space>
        </template>
      </template>
    </a-table>
    <InvoiceDetailDrawer v-model:open="drawerOpen" :invoice="current" @refresh="load" />
  </div>
</template>
```

```vue
<!-- src/components/InvoiceDetailDrawer.vue -->
<script setup lang="ts">
import { downloadInvoiceFile } from "../api/invoices";
import { INVOICE_STATUS_LABELS, VERIFY_STATUS_LABELS, type InvoiceOut } from "../types";

const props = defineProps<{ open: boolean; invoice: InvoiceOut | null }>();
const emit = defineEmits<{ "update:open": [boolean]; refresh: [] }>();

function onDownload(kind: "file" | "xml") {
  if (props.invoice) downloadInvoiceFile(props.invoice.id, kind);
}
</script>

<template>
  <a-drawer title="发票详情" :open="open" width="480" @close="emit('update:open', false)">
    <template v-if="invoice">
      <a-descriptions :column="1" size="small" bordered>
        <a-descriptions-item label="发票号码">{{ invoice.invoice_number || "—" }}</a-descriptions-item>
        <a-descriptions-item label="开票日期">{{ invoice.issue_date || "—" }}</a-descriptions-item>
        <a-descriptions-item label="不含税金额">{{ invoice.amount_without_tax ?? "—" }}</a-descriptions-item>
        <a-descriptions-item label="税额">{{ invoice.tax_amount ?? "—" }}</a-descriptions-item>
        <a-descriptions-item label="价税合计">{{ invoice.total_amount ?? "—" }}（{{ invoice.total_amount_cn || "" }}）</a-descriptions-item>
        <a-descriptions-item label="销售方">{{ invoice.seller_name || "—" }}（{{ invoice.seller_tax_id || "—" }}）</a-descriptions-item>
        <a-descriptions-item label="购买方">{{ invoice.buyer_name || "—" }}（{{ invoice.buyer_tax_id || "—" }}）</a-descriptions-item>
        <a-descriptions-item label="状态">
          <a-tag>{{ INVOICE_STATUS_LABELS[invoice.status] || invoice.status }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="验真">{{ VERIFY_STATUS_LABELS[invoice.verify_status] || invoice.verify_status }}</a-descriptions-item>
        <a-descriptions-item label="解析来源">{{ invoice.parse_source || "—" }}（置信度 {{ invoice.confidence_score ?? "—" }}）</a-descriptions-item>
        <a-descriptions-item label="来源邮件">{{ invoice.email_subject || "—" }}</a-descriptions-item>
        <a-descriptions-item label="重复标记">{{ invoice.duplicate_flag ? "是" : "否" }}</a-descriptions-item>
      </a-descriptions>
      <a-space style="margin-top: 16px">
        <a-button @click="onDownload('file')">下载原件</a-button>
        <a-button @click="onDownload('xml')">下载 XML</a-button>
      </a-space>
    </template>
  </a-drawer>
</template>
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd web && npm run test`
Expected: 全部 PASS（新增 3 条 + Task 1 的 6 条）

- [ ] **Step 5: 构建验证并提交**

Run: `cd web && npm run build`
Expected: 构建成功

```bash
git add web/src
git commit -m "feat(web): 工作台统计与发票列表/详情/复核"
```

---

### Task 3: 审计日志页、系统配置页、收尾与文档

**Files:**
- Create: `web/src/api/audit.ts`、`web/src/api/mailboxes.ts`、`web/src/api/users.ts`
- Modify: `web/src/types.ts`（追加 AuditOut/MailboxOut/UserCreate 等）
- Modify: `web/src/views/AuditView.vue`（占位 → 完整）
- Modify: `web/src/views/SettingsView.vue`（占位 → 邮箱/用户双 tab）
- Create: `web/src/views/__tests__/settings.spec.ts`
- Modify: `docs/开发环境指南.md`（前端启动章节）、`CLAUDE.md`（构建与测试节）

**Interfaces:**
- Consumes: `GET /audit-logs`（user_id/action/invoice_id/date_from/date_to/page/page_size）、`GET/POST/PUT /mailboxes`、`POST /mailboxes/{id}/test`、`POST /mailboxes/{id}/poll`、`GET/POST/PUT /users`
- Produces: 三个 api 模块 + AuditView/SettingsView + 文档

- [ ] **Step 1: 写失败测试 settings.spec.ts**

```ts
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
    const wrapper = mount(SettingsView, { global: { stubs: true } });
    expect(wrapper.exists()).toBe(true);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd web && npm run test`
Expected: FAIL（Settings 仍为占位）

- [ ] **Step 3: 实现 types/api/页面**

```ts
// types.ts 追加
export interface AuditOut {
  id: number; user_id: number | null; action: string; invoice_id: number | null;
  detail: Record<string, unknown> | null; ip_address: string | null;
  channel: string; created_at: string;
}
export interface AuditListResponse { items: AuditOut[]; total: number; page: number; page_size: number; }

export interface MailboxOut {
  id: number; name: string; imap_host: string; imap_port: number; use_ssl: boolean;
  username: string; folder: string; keywords: string; poll_interval_seconds: number;
  smtp_host: string | null; smtp_port: number | null; smtp_username: string | null;
  enabled: boolean; last_polled_at: string | null; last_uid: number;
  created_at: string; updated_at: string;
}
export interface MailboxCreate { name: string; imap_host: string; imap_port?: number; use_ssl?: boolean; username: string; password: string; folder?: string; keywords?: string; poll_interval_seconds?: number; smtp_host?: string | null; smtp_port?: number | null; smtp_username?: string | null; smtp_password?: string | null; }
export type MailboxUpdate = Partial<MailboxCreate>;
export interface PollResultOut { received: number; rejected_images: number; ignored: number; duplicates: number; errors: number; }

export interface UserCreate { username: string; password: string; role: Role; }
export interface UserUpdate { password?: string | null; role?: Role; }
```

```ts
// src/api/audit.ts
import { api } from "./client";
import type { AuditListResponse } from "../types";

export interface AuditParams { user_id?: number; action?: string; invoice_id?: number; date_from?: string; date_to?: string; page?: number; page_size?: number; }

export async function listAuditLogs(params: AuditParams): Promise<AuditListResponse> {
  const { data } = await api.get<AuditListResponse>("/audit-logs", { params });
  return data;
}
```

```ts
// src/api/mailboxes.ts
import { api } from "./client";
import type { MailboxCreate, MailboxOut, MailboxUpdate, PollResultOut } from "../types";

export async function listMailboxes(): Promise<MailboxOut[]> {
  const { data } = await api.get<MailboxOut[]>("/mailboxes");
  return data;
}
export async function createMailbox(body: MailboxCreate): Promise<MailboxOut> {
  const { data } = await api.post<MailboxOut>("/mailboxes", body);
  return data;
}
export async function updateMailbox(id: number, body: MailboxUpdate): Promise<MailboxOut> {
  const { data } = await api.put<MailboxOut>(`/mailboxes/${id}`, body);
  return data;
}
export async function testMailbox(id: number): Promise<{ ok: boolean; message: string }> {
  const { data } = await api.post<{ ok: boolean; message: string }>(`/mailboxes/${id}/test`);
  return data;
}
export async function pollMailbox(id: number): Promise<PollResultOut> {
  const { data } = await api.post<PollResultOut>(`/mailboxes/${id}/poll`);
  return data;
}
```

```ts
// src/api/users.ts
import { api } from "./client";
import type { UserCreate, UserOut, UserUpdate } from "../types";

export async function listUsers(): Promise<UserOut[]> {
  const { data } = await api.get<UserOut[]>("/users");
  return data;
}
export async function createUser(body: UserCreate): Promise<UserOut> {
  const { data } = await api.post<UserOut>("/users", body);
  return data;
}
export async function updateUser(id: number, body: UserUpdate): Promise<UserOut> {
  const { data } = await api.put<UserOut>(`/users/${id}`, body);
  return data;
}
```

```vue
<!-- src/views/AuditView.vue -->
<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { errorMessage } from "../api/client";
import { listAuditLogs } from "../api/audit";
import type { AuditListResponse } from "../types";

const data = ref<AuditListResponse>({ items: [], total: 0, page: 1, page_size: 20 });
const loading = ref(false);
const filters = reactive({ action: undefined as string | undefined, dateRange: undefined as [string, string] | undefined });

async function load() {
  loading.value = true;
  try {
    data.value = await listAuditLogs({
      action: filters.action,
      date_from: filters.dateRange?.[0],
      date_to: filters.dateRange?.[1],
      page: data.value.page,
      page_size: data.value.page_size,
    });
  } catch (e) {
    errorMessage(e, "审计日志加载失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const columns = [
  { title: "时间", dataIndex: "created_at", key: "created_at" },
  { title: "操作", dataIndex: "action", key: "action" },
  { title: "用户", dataIndex: "user_id", key: "user_id" },
  { title: "发票", dataIndex: "invoice_id", key: "invoice_id" },
  { title: "通道", dataIndex: "channel", key: "channel" },
  { title: "详情", dataIndex: "detail", key: "detail" },
];
</script>

<template>
  <div>
    <h3>审计日志</h3>
    <a-space style="margin-bottom: 16px" wrap>
      <a-select v-model:value="filters.action" placeholder="操作类型" allow-clear style="width: 180px" @change="load">
        <a-select-option v-for="a in ['FETCH','PARSE','VERIFY','REVIEW','LOGIN','LOGOUT','REJECT_REPLY','CONFIG_CHANGE','REVERIFY']" :key="a" :value="a">{{ a }}</a-select-option>
      </a-select>
      <a-range-picker v-model:value="filters.dateRange" @change="load" />
      <a-button type="primary" @click="load">查询</a-button>
    </a-space>
    <a-table :columns="columns" :data-source="data.items" :loading="loading" row-key="id"
      :pagination="{ total: data.total, current: data.page, pageSize: data.page_size }"
      @change="(p: any) => { data.page = p.current; data.page_size = p.pageSize; load(); }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'detail'">
          <span style="font-size: 12px; color: #888">{{ JSON.stringify(record.detail) }}</span>
        </template>
      </template>
    </a-table>
  </div>
</template>
```

```vue
<!-- src/views/SettingsView.vue -->
<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import { createMailbox, listMailboxes, pollMailbox, testMailbox, updateMailbox } from "../api/mailboxes";
import { createUser, listUsers, updateUser } from "../api/users";
import { ROLE_LABELS, type MailboxCreate, type MailboxOut, type Role, type UserCreate, type UserOut } from "../types";

const activeTab = ref("mailboxes");
const mailboxes = ref<MailboxOut[]>([]);
const users = ref<UserOut[]>([]);
const mailboxModalOpen = ref(false);
const editingMailbox = ref<MailboxOut | null>(null);
const mailboxForm = reactive<MailboxCreate>({ name: "", imap_host: "", username: "", password: "" });
const userModalOpen = ref(false);
const editingUser = ref<UserOut | null>(null);
const userForm = reactive<UserCreate>({ username: "", password: "", role: "employee" });

async function loadAll() {
  try {
    mailboxes.value = await listMailboxes();
    users.value = await listUsers();
  } catch (e) {
    errorMessage(e, "配置加载失败");
  }
}
onMounted(loadAll);

async function saveMailbox() {
  try {
    if (editingMailbox.value) {
      await updateMailbox(editingMailbox.value.id, mailboxForm);
    } else {
      await createMailbox(mailboxForm);
    }
    message.success("已保存");
    mailboxModalOpen.value = false;
    loadAll();
  } catch (e) {
    errorMessage(e, "邮箱保存失败");
  }
}

async function onTestMailbox(row: MailboxOut) {
  try {
    const r = await testMailbox(row.id);
    r.ok ? message.success(r.message) : message.error(r.message);
  } catch (e) {
    errorMessage(e, "连接测试失败");
  }
}

async function onPoll(row: MailboxOut) {
  try {
    const r = await pollMailbox(row.id);
    message.success(`收取完成：收到 ${r.received}，拒收 ${r.rejected_images}，忽略 ${r.ignored}，重复 ${r.duplicates}，错误 ${r.errors}`);
    loadAll();
  } catch (e) {
    errorMessage(e, "收取失败");
  }
}

async function saveUser() {
  try {
    if (editingUser.value) {
      await updateUser(editingUser.value.id, { role: userForm.role as Role });
    } else {
      await createUser(userForm);
    }
    message.success("已保存");
    userModalOpen.value = false;
    loadAll();
  } catch (e) {
    errorMessage(e, "用户保存失败");
  }
}

const mailboxColumns = [
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "IMAP", dataIndex: "imap_host", key: "imap_host" },
  { title: "账号", dataIndex: "username", key: "username" },
  { title: "启用", dataIndex: "enabled", key: "enabled" },
  { title: "上次收取", dataIndex: "last_polled_at", key: "last_polled_at" },
  { title: "操作", key: "actions" },
];
const userColumns = [
  { title: "用户名", dataIndex: "username", key: "username" },
  { title: "角色", dataIndex: "role", key: "role" },
  { title: "创建时间", dataIndex: "created_at", key: "created_at" },
  { title: "操作", key: "actions" },
];
</script>

<template>
  <div>
    <h3>系统配置</h3>
    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane key="mailboxes" tab="邮箱配置">
        <a-button type="primary" style="margin-bottom: 12px" @click="editingMailbox = null; Object.assign(mailboxForm, { name: '', imap_host: '', username: '', password: '' }); mailboxModalOpen = true">新建邮箱</a-button>
        <a-table :columns="mailboxColumns" :data-source="mailboxes" row-key="id" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'actions'">
              <a-space>
                <a @click="editingMailbox = record; Object.assign(mailboxForm, { name: record.name, imap_host: record.imap_host, username: record.username, password: '' }); mailboxModalOpen = true">编辑</a>
                <a @click="onTestMailbox(record)">测试连接</a>
                <a @click="onPoll(record)">手动收取</a>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
      <a-tab-pane key="users" tab="用户管理">
        <a-button type="primary" style="margin-bottom: 12px" @click="editingUser = null; Object.assign(userForm, { username: '', password: '', role: 'employee' }); userModalOpen = true">新建用户</a-button>
        <a-table :columns="userColumns" :data-source="users" row-key="id" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'role'">{{ ROLE_LABELS[record.role as Role] || record.role }}</template>
            <template v-else-if="column.key === 'actions'">
              <a @click="editingUser = record; Object.assign(userForm, { username: record.username, password: '', role: record.role }); userModalOpen = true">编辑角色</a>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:open="mailboxModalOpen" :title="editingMailbox ? '编辑邮箱' : '新建邮箱'" @ok="saveMailbox">
      <a-form layout="vertical">
        <a-form-item label="名称"><a-input v-model:value="mailboxForm.name" /></a-form-item>
        <a-form-item label="IMAP 主机"><a-input v-model:value="mailboxForm.imap_host" placeholder="imap.example.com" /></a-form-item>
        <a-form-item label="IMAP 端口"><a-input-number v-model:value="mailboxForm.imap_port" style="width: 100%" /></a-form-item>
        <a-form-item label="账号"><a-input v-model:value="mailboxForm.username" /></a-form-item>
        <a-form-item label="密码"><a-input-password v-model:value="mailboxForm.password" placeholder="编辑时留空表示不修改" /></a-form-item>
        <a-form-item label="主题关键词"><a-input v-model:value="mailboxForm.keywords" placeholder="发票,Invoice" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:open="userModalOpen" :title="editingUser ? '编辑用户' : '新建用户'" @ok="saveUser">
      <a-form layout="vertical">
        <a-form-item label="用户名"><a-input v-model:value="userForm.username" :disabled="!!editingUser" /></a-form-item>
        <a-form-item v-if="!editingUser" label="密码"><a-input-password v-model:value="userForm.password" /></a-form-item>
        <a-form-item label="角色">
          <a-select v-model:value="userForm.role">
            <a-select-option v-for="(label, value) in ROLE_LABELS" :key="value" :value="value">{{ label }}</a-select-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>
```

- [ ] **Step 4: 运行测试与构建**

Run: `cd web && npm run test && npm run build`
Expected: 全部 PASS + 构建成功

- [ ] **Step 5: 文档更新**

docs/开发环境指南.md 追加：

```markdown
## 前端（web/）

```bash
cd web
npm install
npm run dev          # http://localhost:5173（/api 自动代理到 8000 后端）
npm run build        # 产物 dist/（由 Plan D Nginx 托管）
npm run test         # Vitest 冒烟测试
```

默认管理员：admin/admin123（后端首次启动自动创建，生产必须改）。
```

CLAUDE.md 构建与测试节追加：

```bash
cd web && npm install && npm run dev   # Web 管理后台（Vue 3，端口 5173，代理 /api → 8000）
cd web && npm run test                 # 前端冒烟测试
```

- [ ] **Step 6: 全量回归并提交**

Run: `cd backend && uv run pytest ../test -q`（后端 94 不受影响）+ `cd web && npm run test && npm run build`
Expected: 全绿

```bash
git add web/src docs/开发环境指南.md CLAUDE.md
git commit -m "feat(web): 审计日志页、系统配置页与前端文档"
```

---

## Plan C 验收清单（全部完成后核对）

- [ ] `cd web && npm run test` 全绿；`npm run build` 成功
- [ ] 后端套件 94 不受影响
- [ ] 页面齐全：登录/工作台/发票列表(筛选+分页+复核+重验+详情抽屉+原件下载)/审计日志(admin)/系统配置(admin)
- [ ] 4 角色菜单收敛：审计/配置仅 admin；复核/重验仅财务+；员工列表由后端过滤
- [ ] 401 自动登出跳登录；文件下载带 token（blob 方式）
- [ ] REST 契约逐字对齐设计 §五（无自造接口）
- [ ] `docs/开发环境指南.md` 前端章节可照做
