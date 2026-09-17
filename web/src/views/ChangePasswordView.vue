<!-- 修改密码：顶栏「自愿改密」与「管理员重置后的强制改密」共用本页
     （一个实现，避免两套表单；强制时由路由守卫把人送到这里） -->
<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import { changeOwnPassword } from "../api/auth";
import { useAuthStore } from "../stores/auth";
import PageHeader from "../components/PageHeader.vue";

const auth = useAuthStore();
const router = useRouter();

// 强制改密：管理员刚重置过密码，改完才能用其他功能（后端默认全拦、白名单放行）
const forced = computed(() => auth.user?.must_change_password === true);

const form = reactive({ old_password: "", new_password: "", confirm: "" });
const loading = ref(false);

async function onSubmit() {
  // 两次输入一致性只能前端查（后端无 confirm 字段）；其余交给 service 层抛中文错。
  if (form.new_password !== form.confirm) {
    message.warning("两次输入的新密码不一致");
    return;
  }
  loading.value = true;
  try {
    await changeOwnPassword(form.old_password, form.new_password);
    await auth.loadMe(); // 刷新 must_change_password，守卫随之放行
    message.success("密码已修改");
    router.push("/");
  } catch (e) {
    errorMessage(e, "修改失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="narrow">
    <PageHeader title="修改密码" />

    <a-alert
      v-if="forced"
      type="warning"
      show-icon
      class="mb-4"
      message="请先修改密码"
      description="管理员已重置你的密码。为安全起见，修改后才能使用其他功能。"
    />

    <a-form layout="vertical">
      <a-form-item label="原密码">
        <a-input-password v-model:value="form.old_password" placeholder="当前使用的密码" />
      </a-form-item>
      <a-form-item label="新密码">
        <a-input-password v-model:value="form.new_password" placeholder="至少 8 位" />
      </a-form-item>
      <a-form-item label="确认新密码">
        <a-input-password v-model:value="form.confirm" placeholder="再输入一次" />
      </a-form-item>
      <a-space>
        <a-button type="primary" :loading="loading" @click="onSubmit">确认修改</a-button>
        <a-button v-if="!forced" @click="router.push('/')">取消</a-button>
      </a-space>
    </a-form>
  </div>
</template>

<style scoped>
.narrow { max-width: 460px; }
.mb-4 { margin-bottom: var(--space-4); }
</style>
