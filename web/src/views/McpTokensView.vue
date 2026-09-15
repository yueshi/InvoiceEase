<!-- 我的令牌：MCP 访问令牌自助签发/撤销（管理员可代发与查看全部）
     设计见 design/2026-09-13-MCP身份与权限设计.md §10 —— 一人一令牌是主路径 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import {
  fetchMcpScopes,
  issueMcpToken,
  listMcpTokens,
  revokeMcpToken,
} from "../api/mcpTokens";
import { useAuthStore } from "../stores/auth";
import { ROLE_LABELS, type McpTokenOut, type Role } from "../types";

const auth = useAuthStore();
const isAdmin = computed(() => auth.role === "admin");

const rows = ref<McpTokenOut[]>([]);
const loading = ref(false);
const allUsers = ref(false);

const scopes = ref<string[]>([]);
const roleDefaults = ref<Record<string, string[]>>({});

// 签发弹窗
const issueOpen = ref(false);
const issuing = ref(false);
const issueForm = reactive({
  name: "",
  scopes: [] as string[],
  days: 90,
  never_expires: false,
});
const canUsePreset = computed(() => Boolean(auth.role && roleDefaults.value[auth.role as string]));

// 明文一次性展示：不落库，关闭后不可再取
const issued = ref<{ name: string; plaintext: string } | null>(null);

const columns = [
  { title: "名称", dataIndex: "name", key: "name" },
  ...(allUsers.value ? [{ title: "归属", dataIndex: "owner_username", key: "owner_username" }] : []),
  { title: "前缀", dataIndex: "token_prefix", key: "token_prefix", width: 110 },
  { title: "权限", dataIndex: "scopes", key: "scopes" },
  { title: "状态", dataIndex: "state", key: "state", width: 100 },
  { title: "有效期至", dataIndex: "expires_at", key: "expires_at", width: 130 },
  { title: "最后使用", dataIndex: "last_used_at", key: "last_used_at", width: 130 },
  { title: "操作", key: "op", width: 80 },
];

const STATE_LABELS: Record<string, { text: string; color: string }> = {
  active: { text: "有效", color: "green" },
  revoked: { text: "已撤销", color: "default" },
  expired: { text: "已过期", color: "orange" },
};

/** 默认选中本角色预设（管理员一键给自己全量权限） */
function openIssue() {
  issueForm.name = "";
  issueForm.days = 90;
  issueForm.never_expires = false;
  issueForm.scopes = [...(roleDefaults.value[auth.role ?? ""] ?? [])];
  issueOpen.value = true;
}

async function load() {
  loading.value = true;
  try {
    rows.value = await listMcpTokens(allUsers.value);
  } catch (e) {
    errorMessage(e, "令牌加载失败");
  } finally {
    loading.value = false;
  }
}

async function onIssue() {
  if (!issueForm.name.trim()) {
    message.warning("请填写令牌名称（如「张三的 WorkBuddy」）");
    return;
  }
  if (!issueForm.scopes.length) {
    message.warning("请至少选择一个权限");
    return;
  }
  issuing.value = true;
  try {
    const res = await issueMcpToken({
      name: issueForm.name.trim(),
      scopes: issueForm.scopes,
      expires_in_days: issueForm.never_expires ? undefined : issueForm.days,
      never_expires: issueForm.never_expires,
    });
    issueOpen.value = false;
    issued.value = { name: res.token.name, plaintext: res.plaintext };
    await load();
  } catch (e) {
    errorMessage(e, "签发失败");
  } finally {
    issuing.value = false;
  }
}

async function onRevoke(r: McpTokenOut) {
  try {
    await revokeMcpToken(r.id);
    message.success(`已撤销「${r.name}」，下次调用立即失效`);
    await load();
  } catch (e) {
    errorMessage(e, "撤销失败");
  }
}

async function copyToken() {
  if (!issued.value) return;
  try {
    await navigator.clipboard.writeText(issued.value.plaintext);
    message.success("已复制到剪贴板");
  } catch {
    message.error("复制失败，请手动选择复制");
  }
}

function fmt(v: string | null): string {
  return v ? v.replace("T", " ").slice(0, 16) : "—";
}

/** 30 天内到期提示（令牌是长期配置，过期即断，提醒续期） */
function expiringSoon(r: McpTokenOut): boolean {
  if (r.state !== "active" || !r.expires_at) return false;
  return new Date(r.expires_at).getTime() - Date.now() < 30 * 24 * 3600 * 1000;
}

onMounted(async () => {
  await load();
  try {
    const meta = await fetchMcpScopes();
    scopes.value = meta.scopes;
    roleDefaults.value = meta.role_defaults;
  } catch {
    /* 拉不到只影响"一键填充预设"，不影响手选 */
  }
});
</script>

<template>
  <div>
    <h3>我的令牌</h3>
    <p style="color: #666; margin-bottom: 12px">
      生成令牌后在 WorkBuddy 里按用户配置，Agent 的每次调用就会以你的身份执行，
      <b>只能看到你自己的发票与报销单</b>。令牌明文只显示一次，请当场复制。
    </p>

    <a-space style="margin-bottom: 16px" wrap>
      <a-button type="primary" @click="openIssue">签发新令牌</a-button>
      <a-button @click="load">刷新</a-button>
      <a-checkbox v-if="isAdmin" v-model:checked="allUsers" @change="load">
        查看全部用户（管理员）
      </a-checkbox>
    </a-space>

    <a-table :columns="columns" :data-source="rows" :loading="loading" row-key="id" :pagination="false">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'owner_username'">{{ record.owner_username || "—" }}</template>
        <template v-else-if="column.key === 'token_prefix'">
          <code>{{ record.token_prefix }}…</code>
        </template>
        <template v-else-if="column.key === 'scopes'">
          <a-tag v-for="s in record.scopes" :key="s" style="margin-bottom: 2px">{{ s }}</a-tag>
        </template>
        <template v-else-if="column.key === 'state'">
          <a-tag :color="STATE_LABELS[record.state]?.color">
            {{ STATE_LABELS[record.state]?.text || record.state }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'expires_at'">
          <span :style="expiringSoon(record) ? 'color:#d97706' : ''">
            {{ record.expires_at ? fmt(record.expires_at) : "永久" }}
            <template v-if="expiringSoon(record)">（即将到期）</template>
          </span>
        </template>
        <template v-else-if="column.key === 'last_used_at'">{{ fmt(record.last_used_at) }}</template>
        <template v-else-if="column.key === 'op'">
          <a-popconfirm
            v-if="record.state === 'active'"
            title="撤销后该令牌立即失效，需重新签发才能用。确定撤销？"
            ok-text="撤销"
            cancel-text="取消"
            @confirm="onRevoke(record)"
          >
            <a style="color: #cf1322">撤销</a>
          </a-popconfirm>
        </template>
      </template>
    </a-table>

    <!-- 签发 -->
    <a-modal v-model:open="issueOpen" title="签发 MCP 令牌" :confirm-loading="issuing" @ok="onIssue">
      <a-form layout="vertical">
        <a-form-item label="令牌名称">
          <a-input v-model:value="issueForm.name" placeholder="如：张三的 WorkBuddy" />
        </a-form-item>
        <a-form-item label="有效期">
          <a-radio-group v-model:value="issueForm.days" :disabled="issueForm.never_expires">
            <a-radio-button :value="30">30 天</a-radio-button>
            <a-radio-button :value="90">90 天</a-radio-button>
            <a-radio-button :value="180">180 天</a-radio-button>
            <a-radio-button :value="365">365 天</a-radio-button>
          </a-radio-group>
          <a-checkbox v-model:checked="issueForm.never_expires" style="margin-left: 12px">
            永久（不推荐）
          </a-checkbox>
        </a-form-item>
        <a-form-item>
          <template #label>
            <span>权限（scope）</span>
            <a v-if="canUsePreset" style="margin-left: 8px" @click="issueForm.scopes = [...roleDefaults[auth.role ?? '']]">
              按我的角色（{{ ROLE_LABELS[(auth.role as Role) ?? "employee"] }}）填充
            </a>
          </template>
          <a-select
            v-model:value="issueForm.scopes"
            mode="multiple"
            placeholder="至少选一个"
            :options="scopes.map((s) => ({ value: s, label: s }))"
          />
          <div style="color: #888; font-size: 12px; margin-top: 4px">
            权限是令牌的<b>能力上限</b>；能看哪些数据还取决于你的角色。默认按角色预设填充即可。
          </div>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 明文一次性展示 -->
    <a-modal
      :open="Boolean(issued)"
      title="令牌已签发 —— 请立即复制"
      :footer="null"
      :closable="false"
      :mask-closable="false"
      @cancel="issued = null"
    >
      <a-alert
        type="warning"
        show-icon
        style="margin-bottom: 12px"
        message="明文只显示这一次"
        description="令牌以哈希存储，关闭本窗口后无法再次查看。若丢失，只能撤销后重新签发。"
      />
      <p style="margin-bottom: 6px">「{{ issued?.name }}」</p>
      <a-textarea :value="issued?.plaintext" :rows="3" readonly style="font-family: monospace" />
      <a-space style="margin-top: 12px">
        <a-button type="primary" @click="copyToken">复制令牌</a-button>
        <a-button @click="issued = null">我已复制，关闭</a-button>
      </a-space>
    </a-modal>
  </div>
</template>
