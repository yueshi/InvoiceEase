<!-- 布局骨架：ConfigProvider 设计令牌 + 亮色侧栏（品牌区/菜单）+ 顶栏（页标题/用户菜单）+ .page 容器
     登录页外展示侧边栏 + 顶栏，菜单按角色收敛 -->
<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

// 设计令牌（design/2026-09-16-WebUI布局与样式优化设计.md §2.1）：主色/圆角/底色 + 亮色菜单
const themeConfig = {
  token: {
    colorPrimary: "#2563EB",
    borderRadius: 8,
    colorBgLayout: "#F6F7F9",
  },
  components: {
    Menu: {
      // 注意：antdv 4 的 Menu token 是 colorItemText/... 命名（v5 的 itemColor* 在此版本静默无效）
      colorItemText: "#5B6472",
      colorItemTextSelected: "#2563EB",
      colorItemBgSelected: "#EFF6FF",
      radiusItem: 6,
    },
  },
};

// 顶栏页标题：路由 meta.title（router/index.ts 逐路由配置）
const pageTitle = computed(() => (route.meta.title as string) || "发票易");

// 菜单项按角色收敛（computed：登录后角色变化实时生效）
const isFinance = () => ["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "");
const menuItems = computed(() => [
  { key: "/", label: "工作台" },
  { key: "/invoices", label: "发票列表" },
  { key: "/expenses", label: "我的报销" },
  { key: "/mcp-tokens", label: "我的令牌" },
  ...(isFinance() ? [{ key: "/receipts", label: "银行回单" }] : []),
  ...(isFinance() ? [{ key: "/tasks", label: "异步任务" }] : []),
  ...(auth.isAdmin ? [{ key: "/audit", label: "审计日志" }, { key: "/ops", label: "运维" }, { key: "/settings", label: "系统配置" }] : []),
]);

function onLogout() {
  auth.logout().finally(() => router.push("/login"));
}

// 菜单点击：按 key 路由跳转（显式类型，满足 vue-tsc 严格模式）
function onMenuClick(info: { key: string }) {
  router.push(info.key);
}
</script>

<template>
  <a-config-provider :theme="themeConfig">
    <a-layout v-if="route.path !== '/login'" class="app-layout">
      <a-layout-sider :width="208" class="app-sider">
        <div class="brand">
          <div class="brand-logo">发</div>
          <span class="brand-name">发票易</span>
        </div>
        <a-menu :selected-keys="[route.path]" :items="menuItems" @click="onMenuClick" />
      </a-layout-sider>
      <a-layout>
        <a-layout-header class="app-header">
          <span class="app-header-title">{{ pageTitle }}</span>
          <a-dropdown :trigger="['click']">
            <span class="user-chip">
              <span class="user-avatar">{{ (auth.user?.username || "?").slice(0, 1) }}</span>
              <span>{{ auth.user?.username }}</span>
            </span>
            <template #overlay>
              <a-menu>
                <a-menu-item key="change-password" @click="router.push('/change-password')">修改密码</a-menu-item>
                <a-menu-item key="logout" @click="onLogout">退出登录</a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </a-layout-header>
        <a-layout-content class="app-content">
          <div class="page">
            <router-view />
          </div>
        </a-layout-content>
      </a-layout>
    </a-layout>
    <router-view v-else />
  </a-config-provider>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
}
/* 亮色侧栏：白底 + 右分隔线（替代 theme="dark"） */
.app-sider {
  background: #fff;
  border-right: 1px solid #eef0f3;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: var(--space-4);
}
.brand-logo {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand-name {
  font-size: 16px;
  font-weight: 700;
}
/* 顶栏 56px：左页标题 + 右用户菜单 */
.app-header {
  height: 56px;
  line-height: normal;
  padding: 0 var(--space-5);
  background: #fff;
  border-bottom: 1px solid #eef0f3;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.app-header-title {
  font-size: 15px;
  font-weight: 600;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}
.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: #eff6ff;
  color: #2563eb;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.app-content {
  padding: 0; /* .page 自带内边距 */
}
</style>
