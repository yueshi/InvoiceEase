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
  <div style="max-width: 360px; margin: 120px auto">
    <h2 style="text-align: center">发票易</h2>
    <a-form :model="formState" @finish="() => onSubmit()">
      <a-form-item>
        <a-input v-model:value="formState.username" placeholder="用户名" />
      </a-form-item>
      <a-form-item>
        <a-input-password v-model:value="formState.password" placeholder="密码" />
      </a-form-item>
      <a-button type="primary" html-type="submit" block :loading="loading">登录</a-button>
    </a-form>
  </div>
</template>
