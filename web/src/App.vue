<!-- 布局骨架：登录页外展示侧边栏 + 顶栏，菜单按角色收敛 -->
<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

// 菜单项按角色收敛（computed：登录后角色变化实时生效）
const isFinance = () => ["finance_staff", "finance_manager", "admin"].includes(auth.role ?? "");
const menuItems = computed(() => [
  { key: "/", label: "工作台" },
  { key: "/invoices", label: "发票列表" },
  { key: "/expenses", label: "我的报销" },
  { key: "/mcp-tokens", label: "我的令牌" },
  ...(isFinance() ? [{ key: "/receipts", label: "银行回单" }] : []),
  ...(isFinance() ? [{ key: "/tasks", label: "异步任务" }] : []),
  ...(auth.isAdmin ? [{ key: "/audit", label: "审计日志" }, { key: "/settings", label: "系统配置" }] : []),
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
  <a-layout v-if="route.path !== '/login'" style="min-height: 100vh">
    <a-layout-sider>
      <div style="color: #fff; padding: 16px; font-weight: 600">发票易</div>
      <a-menu theme="dark" :selected-keys="[route.path]" :items="menuItems" @click="onMenuClick" />
    </a-layout-sider>
    <a-layout>
      <a-layout-header style="background: #fff; display: flex; justify-content: flex-end; align-items: center; gap: 12px">
        <span>{{ auth.user?.username }}</span>
        <a-button @click="router.push('/change-password')">修改密码</a-button>
        <a-button @click="onLogout">退出登录</a-button>
      </a-layout-header>
      <a-layout-content style="padding: 24px">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
  <router-view v-else />
</template>
