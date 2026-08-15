<!-- 系统配置：邮箱配置 + 用户管理 双 tab -->
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
        <a-button type="primary" style="margin-bottom: 12px" @click="editingMailbox = null; Object.assign(mailboxForm, { name: '', imap_host: '', username: '', password: '', imap_port: undefined, keywords: '' }); mailboxModalOpen = true">新建邮箱</a-button>
        <a-table :columns="mailboxColumns" :data-source="mailboxes" row-key="id" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'actions'">
              <a-space>
                <a @click="editingMailbox = record; Object.assign(mailboxForm, { name: record.name, imap_host: record.imap_host, imap_port: record.imap_port, username: record.username, password: '', keywords: record.keywords }); mailboxModalOpen = true">编辑</a>
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
