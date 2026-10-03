<!-- 登录页：表单提交后写入 auth store 并跳转
     注意：a-form 必须绑 :model，否则 ant-design-vue 的 handleSubmit 不会 emit finish（见 Form.js if (props.model)） -->
<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { errorMessage } from "../api/client";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();
const formState = reactive({ username: "", password: "" });
const loading = ref(false);

async function onSubmit(u?: string, p?: string) {
  loading.value = true;
  try {
    await auth.login(u ?? formState.username, p ?? formState.password);
    router.push(String(route.query.redirect || "/"));
  } catch (e) {
    errorMessage(e, "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="login-logo">发</div>
        <h1 class="login-title">发票易</h1>
        <p class="login-desc">电子发票自动化：收取 · 解析 · 验真 · 归档</p>
      </div>
      <a-alert
        v-if="route?.query?.expired"
        type="warning"
        show-icon
        message="登录链接已失效，请重新登录"
        style="margin-bottom: var(--space-4)"
      />
      <a-form :model="formState" @finish="() => onSubmit()">
        <a-form-item>
          <a-input v-model:value="formState.username" placeholder="用户名" size="large" />
        </a-form-item>
        <a-form-item>
          <a-input-password v-model:value="formState.password" placeholder="密码" size="large" />
        </a-form-item>
        <a-button type="primary" html-type="submit" block :loading="loading">登录</a-button>
      </a-form>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  background: #f6f7f9;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-5);
}
.login-card {
  width: 360px;
  padding: var(--space-6);
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 8px 24px rgba(16, 24, 40, 0.08);
}
.login-brand {
  text-align: center;
  margin-bottom: var(--space-5);
}
.login-logo {
  width: 48px;
  height: 48px;
  margin: 0 auto var(--space-3);
  border-radius: 12px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  font-size: 24px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}
.login-desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--c-sub);
}
</style>
