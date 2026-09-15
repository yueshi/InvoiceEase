<!-- 系统配置：邮箱配置 + 用户管理 + 常用税号/公司 + 常用银行账号 四 tab -->
<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { Modal, message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import { createCompanyInfo, deleteCompanyInfo, listCompanyInfos, updateCompanyInfo } from "../api/companyInfos";
import { createBankAccount, deleteBankAccount, listBankAccounts, updateBankAccount } from "../api/bankAccounts";
import { createMailbox, listMailboxes, pollMailbox, testMailbox, updateMailbox } from "../api/mailboxes";
import { createUser, listUsers, resetUserPassword, resumeUser, suspendUser, updateUser } from "../api/users";
import { BANK_LABELS, COMPANY_KIND_LABELS, ROLE_LABELS, USER_STATUS_LABELS, type BankAccountCreate, type BankAccountOut, type CompanyInfoCreate, type CompanyInfoOut, type MailboxCreate, type MailboxOut, type MailboxUpdate, type Role, type UserCreate, type UserOut, type UserStatus } from "../types";

const TAX_ID_RE = /^[0-9A-Z]{18}$/;

const activeTab = ref("mailboxes");
const mailboxes = ref<MailboxOut[]>([]);
const users = ref<UserOut[]>([]);
const mailboxModalOpen = ref(false);
const editingMailbox = ref<MailboxOut | null>(null);
const mailboxForm = reactive({
  name: "", mailbox_type: "imap", imap_host: "", imap_port: 993 as number | undefined,
  username: "", password: "", keywords: "发票,Invoice",
  agently_workspace: "", agently_token: "",
});
const userModalOpen = ref(false);
const editingUser = ref<UserOut | null>(null);
const userForm = reactive<UserCreate>({ username: "", password: "", role: "employee" });
const companyInfos = ref<CompanyInfoOut[]>([]);
const companyModalOpen = ref(false);
const editingCompanyId = ref<number | null>(null);
const companyForm = reactive({ name: "", tax_id: "", kind: "other", is_default: false, remark: "", bank_account: "" });
// 类型切为非本司时联动清空默认标记，避免 checkbox 禁用但仍勾选的视觉误导
watch(() => companyForm.kind, (kind) => {
  if (kind !== "self") companyForm.is_default = false;
});
// 常用企业银行账号（本司账户；回单「本司账户行」判定）
const bankAccounts = ref<BankAccountOut[]>([]);
const bankModalOpen = ref(false);
const editingBankId = ref<number | null>(null);
const bankForm = reactive({ account_no: "", account_name: "", bank_name: "", bank_code: "", remark: "", is_default: false, enabled: true });
// 银行下拉可选；留空则由后端按开户行自动识别
const BANK_OPTIONS = Object.entries(BANK_LABELS).map(([value, label]) => ({ value, label }));
const bankColumns = [
  { title: "账号", dataIndex: "account_no", key: "account_no" },
  { title: "户名", dataIndex: "account_name", key: "account_name" },
  { title: "银行", key: "bank_code", width: 110 },   // 短名（识别/手选），便于一眼分辨
  { title: "开户行", dataIndex: "bank_name", key: "bank_name" },
  { title: "备注", dataIndex: "remark", key: "remark" },
  { title: "默认", dataIndex: "is_default", key: "is_default" },
  { title: "状态", dataIndex: "enabled", key: "enabled" },
  { title: "操作", key: "actions" },
];

function openBankModal(record: BankAccountOut | null) {
  editingBankId.value = record ? record.id : null;
  Object.assign(bankForm, {
    account_no: record?.account_no ?? "",
    account_name: record?.account_name ?? "",
    bank_name: record?.bank_name ?? "",
    bank_code: record?.bank_code ?? "",
    remark: record?.remark ?? "",
    is_default: record?.is_default ?? false,
    enabled: record?.enabled ?? true,
  });
  bankModalOpen.value = true;
}

async function saveBankAccount() {
  if (!/^[0-9\s\-]{6,40}$/.test(bankForm.account_no.trim())) {
    message.warning("账号须为 6-32 位数字（可含空格/连字符）");
    return;
  }
  try {
    const body: BankAccountCreate = {
      account_no: bankForm.account_no.trim(),
      account_name: bankForm.account_name.trim() || null,
      bank_name: bankForm.bank_name.trim() || null,
      bank_code: bankForm.bank_code || null, // 留空 → 后端按开户行自动识别
      remark: bankForm.remark.trim() || null,
      is_default: bankForm.is_default,
      enabled: bankForm.enabled,
    };
    if (editingBankId.value) await updateBankAccount(editingBankId.value, body);
    else await createBankAccount(body);
    message.success("已保存");
    bankModalOpen.value = false;
    loadAll();
  } catch (e) {
    errorMessage(e, "银行账号保存失败");
  }
}

async function onDeleteBankAccount(record: BankAccountOut) {
  try {
    await deleteBankAccount(record.id);
    message.success("已删除");
    loadAll();
  } catch (e) {
    errorMessage(e, "删除失败");
  }
}

async function loadAll() {
  // 各项独立加载：任一失败不影响其余渲染，各自单独提示
  try {
    mailboxes.value = await listMailboxes();
  } catch (e) {
    errorMessage(e, "邮箱配置加载失败");
  }
  try {
    users.value = await listUsers();
  } catch (e) {
    errorMessage(e, "用户配置加载失败");
  }
  try {
    companyInfos.value = await listCompanyInfos();
  } catch (e) {
    errorMessage(e, "公司配置加载失败");
  }
  try {
    bankAccounts.value = await listBankAccounts();
  } catch (e) {
    errorMessage(e, "银行账号加载失败");
  }
}
onMounted(loadAll);

async function saveMailbox() {
  try {
    const payload: Record<string, unknown> = {
      name: mailboxForm.name,
      mailbox_type: mailboxForm.mailbox_type,
      keywords: mailboxForm.keywords,
    };
    if (mailboxForm.mailbox_type === "imap") {
      Object.assign(payload, {
        imap_host: mailboxForm.imap_host,
        imap_port: mailboxForm.imap_port,
        username: mailboxForm.username,
      });
      if (mailboxForm.password) payload.password = mailboxForm.password; // 编辑时留空不修改
    } else {
      payload.agently_workspace = mailboxForm.agently_workspace || null;
      if (mailboxForm.agently_token) payload.agently_token = mailboxForm.agently_token;
    }
    if (editingMailbox.value) await updateMailbox(editingMailbox.value.id, payload as MailboxUpdate);
    else await createMailbox(payload as unknown as MailboxCreate);
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

function openCompanyModal(record: CompanyInfoOut | null) {
  editingCompanyId.value = record ? record.id : null;
  Object.assign(companyForm, {
    name: record?.name ?? "",
    tax_id: record?.tax_id ?? "",
    kind: record?.kind ?? "other",
    is_default: record?.is_default ?? false,
    remark: record?.remark ?? "",
    bank_account: record?.bank_account ?? "",
  });
  companyModalOpen.value = true;
}

async function saveCompany() {
  if (!companyForm.name.trim()) {
    message.warning("请输入公司名称");
    return;
  }
  if (!TAX_ID_RE.test(companyForm.tax_id)) {
    message.warning("税号需为 18 位字母数字");
    return;
  }
  try {
    const body: CompanyInfoCreate = {
      name: companyForm.name.trim(),
      tax_id: companyForm.tax_id,
      kind: companyForm.kind,
      is_default: companyForm.kind === "self" && companyForm.is_default,
      remark: companyForm.remark || null,
      bank_account: companyForm.bank_account || null,
    };
    if (editingCompanyId.value) await updateCompanyInfo(editingCompanyId.value, body);
    else await createCompanyInfo(body);
    message.success("已保存");
    companyModalOpen.value = false;
    loadAll();
  } catch (e) {
    errorMessage(e, "公司保存失败");
  }
}

function onDeleteCompany(record: CompanyInfoOut) {
  Modal.confirm({
    title: "删除确认",
    content: `确定删除「${record.name}」吗？`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    onOk: async () => {
      try {
        await deleteCompanyInfo(record.id);
        message.success("已删除");
        loadAll();
      } catch (e) {
        errorMessage(e, "删除失败");
      }
    },
  });
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
  { title: "状态", dataIndex: "status", key: "status", width: 120 },
  { title: "创建时间", dataIndex: "created_at", key: "created_at" },
  { title: "操作", key: "actions" },
];

// ---- 用户管理：重置密码 / 暂停恢复 ----
const resetTarget = ref<UserOut | null>(null);
const resetForm = reactive({ generate: true, new_password: "" });
const resetPlaintext = ref<{ username: string; plaintext: string } | null>(null);

function openReset(record: UserOut) {
  resetTarget.value = record;
  resetForm.generate = true;
  resetForm.new_password = "";
}

async function onResetPassword() {
  if (!resetTarget.value) return;
  // 客户端先挡一道：手动指定时校验长度（否则要等服务端 422 才知道，
  // 且后端 schema 的报错是英文的 pydantic 文案）
  if (!resetForm.generate) {
    if (!resetForm.new_password) {
      message.warning("请输入新密码，或改选「自动生成」");
      return;
    }
    if (resetForm.new_password.length < 8) {
      message.warning("新密码至少 8 位");
      return;
    }
  }
  try {
    const res = await resetUserPassword(resetTarget.value.id, {
      generate: resetForm.generate,
      new_password: resetForm.generate ? undefined : resetForm.new_password,
    });
    message.success(`已重置「${resetTarget.value.username}」的密码，其下次登录须先改密`);
    if (res.plaintext) {
      resetPlaintext.value = { username: resetTarget.value.username, plaintext: res.plaintext };
    }
    resetTarget.value = null;
    loadAll();
  } catch (e) {
    errorMessage(e, "重置失败");
  }
}

async function onToggleStatus(record: UserOut) {
  const suspend = record.status === "active";
  try {
    if (suspend) {
      await suspendUser(record.id);
      message.success(`已暂停「${record.username}」：登录、已登录会话与其 Agent 令牌立即失效`);
    } else {
      await resumeUser(record.id);
      message.success(`已恢复「${record.username}」`);
    }
    loadAll();
  } catch (e) {
    errorMessage(e, suspend ? "暂停失败" : "恢复失败");
  }
}

async function copyResetPassword() {
  if (!resetPlaintext.value) return;
  try {
    await navigator.clipboard.writeText(resetPlaintext.value.plaintext);
    message.success("已复制");
  } catch {
    message.error("复制失败，请手动选择复制");
  }
}
const companyColumns = [
  { title: "名称", dataIndex: "name", key: "name" },
  { title: "税号", dataIndex: "tax_id", key: "tax_id" },
  { title: "类型", dataIndex: "kind", key: "kind" },
  { title: "默认", dataIndex: "is_default", key: "is_default" },
  { title: "操作", key: "actions" },
];
</script>

<template>
  <div>
    <h3>系统配置</h3>
    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane key="mailboxes" tab="邮箱配置">
        <a-button type="primary" style="margin-bottom: 12px" @click="editingMailbox = null; Object.assign(mailboxForm, { name: '', mailbox_type: 'imap', imap_host: '', username: '', password: '', imap_port: undefined, keywords: '发票,Invoice', agently_workspace: '', agently_token: '' }); mailboxModalOpen = true">新建邮箱</a-button>
        <a-table :columns="mailboxColumns" :data-source="mailboxes" row-key="id" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'actions'">
              <a-space>
                <a @click="editingMailbox = record; Object.assign(mailboxForm, { name: record.name, mailbox_type: record.mailbox_type, imap_host: record.imap_host, imap_port: record.imap_port, username: record.username, password: '', keywords: record.keywords, agently_workspace: record.agently_workspace || '', agently_token: '' }); mailboxModalOpen = true">编辑</a>
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
            <template v-else-if="column.key === 'status'">
              <a-tag :color="USER_STATUS_LABELS[record.status as UserStatus]?.color">
                {{ USER_STATUS_LABELS[record.status as UserStatus]?.text || record.status }}
              </a-tag>
              <a-tooltip v-if="record.must_change_password" title="管理员已重置其密码，本人尚未修改">
                <a-tag color="orange" style="margin-left: 4px">待改密</a-tag>
              </a-tooltip>
            </template>
            <template v-else-if="column.key === 'actions'">
              <a-space>
                <a @click="editingUser = record; Object.assign(userForm, { username: record.username, password: '', role: record.role }); userModalOpen = true">改角色</a>
                <a @click="openReset(record)">重置密码</a>
                <a-popconfirm
                  :title="record.status === 'active'
                    ? '暂停后该用户将立即无法登录，其 Agent 令牌同时失效。确定？'
                    : '恢复该用户的访问？'"
                  ok-text="确定" cancel-text="取消"
                  @confirm="onToggleStatus(record)"
                >
                  <a :style="record.status === 'active' ? 'color:#cf1322' : ''">
                    {{ record.status === "active" ? "暂停" : "恢复" }}
                  </a>
                </a-popconfirm>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
      <a-tab-pane key="company" tab="常用税号/公司">
        <a-button type="primary" style="margin-bottom: 12px" @click="openCompanyModal(null)">新建公司</a-button>
        <a-table :columns="companyColumns" :data-source="companyInfos" row-key="id" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'kind'">{{ COMPANY_KIND_LABELS[record.kind as string] || record.kind }}</template>
            <template v-else-if="column.key === 'is_default'">{{ record.is_default ? '是' : '' }}</template>
            <template v-else-if="column.key === 'actions'">
              <a-space>
                <a @click="openCompanyModal(record)">编辑</a>
                <a @click="onDeleteCompany(record)">删除</a>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
      <a-tab-pane key="bank" tab="常用银行账号">
        <p style="color: #888; margin-bottom: 12px">
          本司银行账号：回单解析用它判定「本司账户行」（命中时对方户名留空并标记待核对）。
          账号是可靠依据——户名可能与本司全名不一致；停用的账号不参与判定。
        </p>
        <a-button type="primary" style="margin-bottom: 12px" @click="openBankModal(null)">新建账号</a-button>
        <a-table :columns="bankColumns" :data-source="bankAccounts" row-key="id" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'bank_code'">
              <a-tag v-if="record.bank_code">{{ BANK_LABELS[record.bank_code as string] || record.bank_code }}</a-tag>
              <span v-else style="color: #bbb">未识别</span>
            </template>
            <template v-else-if="column.key === 'is_default'">{{ record.is_default ? '是' : '' }}</template>
            <template v-else-if="column.key === 'enabled'">
              <a-tag :color="record.enabled ? 'green' : 'default'">{{ record.enabled ? '启用' : '停用' }}</a-tag>
            </template>
            <template v-else-if="column.key === 'actions'">
              <a-space>
                <a @click="openBankModal(record)">编辑</a>
                <a @click="onDeleteBankAccount(record)">删除</a>
              </a-space>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:open="mailboxModalOpen" :title="editingMailbox ? '编辑邮箱' : '新建邮箱'" @ok="saveMailbox">
      <a-form layout="vertical">
        <a-form-item label="类型">
          <a-radio-group v-model:value="mailboxForm.mailbox_type">
            <a-radio-button value="imap">IMAP</a-radio-button>
            <a-radio-button value="agently">Agently</a-radio-button>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="名称"><a-input v-model:value="mailboxForm.name" /></a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'imap'" label="IMAP 主机"><a-input v-model:value="mailboxForm.imap_host" placeholder="imap.example.com" /></a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'imap'" label="IMAP 端口"><a-input-number v-model:value="mailboxForm.imap_port" style="width: 100%" /></a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'imap'" label="账号"><a-input v-model:value="mailboxForm.username" /></a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'imap'" label="密码"><a-input-password v-model:value="mailboxForm.password" placeholder="编辑时留空表示不修改" /></a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'agently'" label="工作区">
          <a-input v-model:value="mailboxForm.agently_workspace" placeholder="留空 = 默认工作区" />
        </a-form-item>
        <a-form-item v-if="mailboxForm.mailbox_type === 'agently'" label="Access Token">
          <a-input-password v-model:value="mailboxForm.agently_token" placeholder="留空 = 使用服务器本机授权（keychain）" />
        </a-form-item>
        <a-form-item label="主题关键词"><a-input v-model:value="mailboxForm.keywords" placeholder="发票,Invoice" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal v-model:open="userModalOpen" :title="editingUser ? '编辑用户' : '新建用户'" @ok="saveUser">
      <a-form layout="vertical">
        <a-form-item label="用户名"><a-input v-model:value="userForm.username" :disabled="!!editingUser" /></a-form-item>
        <a-form-item v-if="!editingUser" label="密码"><a-input-password v-model:value="userForm.password" placeholder="至少 8 位" /></a-form-item>
        <a-form-item label="角色">
          <a-select v-model:value="userForm.role">
            <a-select-option v-for="(label, value) in ROLE_LABELS" :key="value" :value="value">{{ label }}</a-select-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      :open="Boolean(resetTarget)"
      :title="`重置密码：${resetTarget?.username ?? ''}`"
      @ok="onResetPassword"
      @cancel="resetTarget = null"
    >
      <a-radio-group v-model:value="resetForm.generate">
        <a-radio :value="true">自动生成强密码（推荐）</a-radio>
        <a-radio :value="false">手动指定</a-radio>
      </a-radio-group>
      <a-input-password
        v-if="!resetForm.generate"
        v-model:value="resetForm.new_password"
        placeholder="至少 8 位"
        style="margin-top: 12px"
      />
      <div style="color: #888; font-size: 12px; margin-top: 12px">
        重置后该用户下次登录必须修改密码；其角色不变（降级请用「改角色」）。
      </div>
    </a-modal>

    <a-modal
      :open="Boolean(resetPlaintext)"
      title="密码已重置 —— 请立即复制"
      :footer="null"
      :closable="false"
      :mask-closable="false"
    >
      <a-alert
        type="warning" show-icon style="margin-bottom: 12px"
        message="明文只显示这一次"
        description="密码以哈希存储，关闭后无法再次查看。若丢失，只能再重置一次。"
      />
      <p style="margin-bottom: 6px">「{{ resetPlaintext?.username }}」的新密码：</p>
      <a-textarea :value="resetPlaintext?.plaintext" :rows="2" readonly style="font-family: monospace" />
      <a-space style="margin-top: 12px">
        <a-button type="primary" @click="copyResetPassword">复制密码</a-button>
        <a-button @click="resetPlaintext = null">我已复制，关闭</a-button>
      </a-space>
    </a-modal>

    <a-modal v-model:open="companyModalOpen" :title="editingCompanyId ? '编辑公司' : '新建公司'" @ok="saveCompany">
      <a-form layout="vertical">
        <a-form-item label="公司名称"><a-input v-model:value="companyForm.name" /></a-form-item>
        <a-form-item label="税号"><a-input v-model:value="companyForm.tax_id" placeholder="18 位字母数字" /></a-form-item>
        <a-form-item label="公司类型">
          <a-select v-model:value="companyForm.kind">
            <a-select-option v-for="(label, value) in COMPANY_KIND_LABELS" :key="value" :value="value">{{ label }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="默认">
          <a-checkbox v-model:checked="companyForm.is_default" :disabled="companyForm.kind !== 'self'">本司默认</a-checkbox>
        </a-form-item>
        <a-form-item v-if="companyForm.kind === 'self'" label="本司银行账号">
          <a-input v-model:value="companyForm.bank_account" placeholder="回单解析用：命中本司账户行时对方户名留空并待核对" />
        </a-form-item>
        <a-form-item label="备注"><a-input v-model:value="companyForm.remark" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal
      v-model:open="bankModalOpen"
      :title="editingBankId ? '编辑银行账号' : '新建银行账号'"
      @ok="saveBankAccount"
    >
      <a-form layout="vertical">
        <a-form-item label="账号（6-32 位数字）">
          <a-input v-model:value="bankForm.account_no" placeholder="61050174004100000779" />
        </a-form-item>
        <a-form-item label="户名">
          <a-input v-model:value="bankForm.account_name" placeholder="银行账户户名（可能与公司全名不同）" />
        </a-form-item>
        <a-form-item label="开户行">
          <a-input v-model:value="bankForm.bank_name" placeholder="建行西安蓝湖树小区支行（留空银行则自动识别）" />
        </a-form-item>
        <a-form-item label="银行">
          <a-select v-model:value="bankForm.bank_code" allow-clear placeholder="留空则按开户行自动识别">
            <a-select-option v-for="o in BANK_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="备注"><a-input v-model:value="bankForm.remark" /></a-form-item>
        <a-space>
          <a-checkbox v-model:checked="bankForm.is_default">默认账户</a-checkbox>
          <a-checkbox v-model:checked="bankForm.enabled">启用（参与本司账户行判定）</a-checkbox>
        </a-space>
      </a-form>
    </a-modal>
  </div>
</template>
