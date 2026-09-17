<!-- 运维（兜底，admin）：状态卡片 + 任务/告警/备份/自检四 Tab（设计 §10） -->
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { message } from "ant-design-vue";
import { errorMessage } from "../api/client";
import {
  downloadBackup,
  fetchOpsStatus,
  listBackups,
  listOpsAlerts,
  listTaskRuns,
  runBackup,
  runTask,
} from "../api/ops";
import type { BackupOut, OpsAlertOut, OpsCheckOut, OpsStatusOut, TaskRunOut } from "../types";
import PageHeader from "../components/PageHeader.vue";

const status = ref<OpsStatusOut | null>(null);
const runs = ref<TaskRunOut[]>([]);
const runsTotal = ref(0);
const alerts = ref<OpsAlertOut[]>([]);
const backups = ref<BackupOut[]>([]);
const logLines = ref<string[]>([]);
const activeTab = ref("tasks");
const busy = ref(false);
// 单端点加载失败标记：对应卡片/Tab 隐藏并提示，不整页白屏
const statusFailed = ref(false);
const tasksFailed = ref(false);
const alertsFailed = ref(false);
const backupsFailed = ref(false);

const RUNNABLE = ["mailbox_poll", "review_predict", "monthly_health", "audit_retention", "ops_check", "ops_backup"];

const OUTCOME_META: Record<string, { text: string; color: string }> = {
  success: { text: "成功", color: "green" },
  failed: { text: "失败", color: "orange" },
  error: { text: "异常", color: "red" },
  missed: { text: "错过", color: "default" },
};
const LEVEL_META: Record<string, { text: string; color: string }> = {
  ok: { text: "正常", color: "green" },
  warn: { text: "警告", color: "orange" },
  fail: { text: "异常", color: "red" },
  info: { text: "提示", color: "blue" },
};

// 自检结果（与告警 Tab 同构：级别 tag 列 + 内容列）
const checks = computed(() => (status.value?.checks ?? []) as OpsCheckOut[]);
const checkColumns = [
  { title: "级别", key: "level", width: 90 },
  { title: "检查项", key: "name", width: 180 },
  { title: "说明", dataIndex: "message", key: "message" },
];

function fmtBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(1)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${n} B`;
}

function fmtUptime(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h} 时 ${m} 分` : `${m} 分`;
}

/** 备份名 invoiceease-backup-YYYYMMDD-HHMMSS.tar.gz → 可读时间 "YYYY-MM-DD HH:MM"；不符则原样返回 */
function fmtBackupTime(name: string): string {
  const m = name.match(/invoiceease-backup-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  if (!m) return name;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
}

async function load() {
  // Promise.allSettled：单个端点失败只隐藏对应卡片/Tab 并提示，其余端点照常渲染，不整页白屏
  const settled = await Promise.allSettled([
    fetchOpsStatus(),
    listTaskRuns(),
    listOpsAlerts(),
    listBackups(),
  ]);
  const [s, r, a, b] = settled;
  if (s.status === "fulfilled") {
    status.value = s.value;
    statusFailed.value = false;
  } else {
    status.value = null;
    statusFailed.value = true;
    message.warning("运维状态加载失败，状态卡片已隐藏");
  }
  if (r.status === "fulfilled") {
    runs.value = r.value.items;
    runsTotal.value = r.value.total;
    tasksFailed.value = false;
  } else {
    runs.value = [];
    runsTotal.value = 0;
    tasksFailed.value = true;
    message.warning("任务记录加载失败，任务 Tab 已隐藏");
  }
  if (a.status === "fulfilled") {
    alerts.value = a.value.items;
    alertsFailed.value = false;
  } else {
    alerts.value = [];
    alertsFailed.value = true;
    message.warning("告警列表加载失败，告警 Tab 已隐藏");
  }
  if (b.status === "fulfilled") {
    backups.value = b.value;
    backupsFailed.value = false;
  } else {
    backups.value = [];
    backupsFailed.value = true;
    message.warning("备份列表加载失败，备份 Tab 已隐藏");
  }
  // 当前激活 Tab 的端点失败被隐藏时，切到第一个可见 Tab，避免空 Tab
  if (activeTab.value === "tasks" && tasksFailed.value) activeTab.value = "alerts";
  if (activeTab.value === "alerts" && alertsFailed.value) activeTab.value = "backups";
  if (activeTab.value === "backups" && backupsFailed.value) activeTab.value = "checks";
}

async function onRunTask(name: string) {
  busy.value = true;
  try {
    await runTask(name);
    message.success(`任务 ${name} 已执行`);
    await load();
  } catch (e) {
    errorMessage(e, "任务触发失败");
  } finally {
    busy.value = false;
  }
}

async function onBackup() {
  busy.value = true;
  try {
    const { name } = await runBackup();
    message.success(`备份完成：${name}`);
    await load();
  } catch (e) {
    errorMessage(e, "备份失败");
  } finally {
    busy.value = false;
  }
}

async function loadLog() {
  try {
    logLines.value = await (await import("../api/ops")).fetchLogTail(200);
  } catch (e) {
    errorMessage(e, "日志读取失败");
  }
}

onMounted(load);
</script>

<template>
  <div>
    <PageHeader title="运维管理" desc="系统状态、任务班表执行记录、告警历史、备份与自检（仅管理员）。" />

    <a-row v-if="status && !statusFailed" :gutter="12" class="status-row">
      <a-col :xs="12" :md="4"><a-card size="small"><a-statistic title="版本" :value="status.version" /></a-card></a-col>
      <a-col :xs="12" :md="4"><a-card size="small"><a-statistic title="运行时长" :value="fmtUptime(status.uptime_seconds)" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :xs="12" :md="4"><a-card size="small"><a-statistic title="数据库" :value="fmtBytes(status.storage.db_bytes)" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :xs="12" :md="4"><a-card size="small"><a-statistic title="原件体积" :value="fmtBytes(status.storage.originals_bytes)" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :xs="12" :md="4"><a-card size="small"><a-statistic title="磁盘剩余" :value="`${status.storage.disk_free_percent}%`" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :xs="12" :md="4">
        <a-card size="small">
          <a-statistic title="最近备份" :value="status.last_backup ? fmtBackupTime(status.last_backup.name) : '无'"
                       :value-style="{ fontSize: '16px' }" />
        </a-card>
      </a-col>
    </a-row>

    <a-tabs v-model:activeKey="activeTab">
      <a-tab-pane key="tasks" tab="任务" v-if="!tasksFailed">
        <div class="table-card">
        <div class="wrap-row mb-3">
          <a-button v-for="name in RUNNABLE" :key="name" size="small" :disabled="busy" @click="onRunTask(name)">
            手动执行 {{ name }}
          </a-button>
          <a-button size="small" @click="load">刷新</a-button>
        </div>
        <a-table :data-source="runs" :pagination="{ total: runsTotal, pageSize: 20 }" row-key="id" size="middle">
          <a-table-column title="任务" data-index="task_name" :width="140" />
          <a-table-column title="触发" data-index="trigger" :width="90" />
          <a-table-column title="开始时间" data-index="started_at" :width="170" />
          <a-table-column title="耗时(ms)" data-index="duration_ms" :width="90" />
          <a-table-column title="结果" key="outcome" :width="80">
            <template #default="{ record }">
              <a-tag :color="OUTCOME_META[record.outcome]?.color || 'default'">
                {{ OUTCOME_META[record.outcome]?.text || record.outcome }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="错误" data-index="error" ellipsis />
        </a-table>
        </div>
      </a-tab-pane>

      <a-tab-pane key="alerts" tab="告警" v-if="!alertsFailed">
        <div class="table-card">
        <a-table :data-source="alerts" :pagination="{ pageSize: 20 }" row-key="id" size="middle">
          <a-table-column title="级别" key="severity" :width="90">
            <template #default="{ record }">
              <a-tag :color="record.severity === 'critical' ? 'red' : 'orange'">
                {{ record.severity === "critical" ? "严重" : "警告" }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="规则" data-index="rule_key" :width="180" />
          <a-table-column title="内容" data-index="message" />
          <a-table-column title="触发时间" data-index="fired_at" :width="170" />
        </a-table>
        </div>
      </a-tab-pane>

      <a-tab-pane key="backups" tab="备份" v-if="!backupsFailed">
        <div class="table-card">
        <a-space class="mb-3">
          <a-button type="primary" :disabled="busy" @click="onBackup">立即备份</a-button>
        </a-space>
        <a-table :data-source="backups" :pagination="{ pageSize: 20 }" row-key="name" size="middle">
          <a-table-column title="文件" data-index="name" />
          <a-table-column title="大小" key="size" :width="110">
            <template #default="{ record }">{{ fmtBytes(record.size_bytes) }}</template>
          </a-table-column>
          <a-table-column title="时间" data-index="created_at" :width="170" />
          <a-table-column title="操作" key="act" :width="90">
            <template #default="{ record }">
              <a-button type="link" size="small" @click="downloadBackup(record.name)">下载</a-button>
            </template>
          </a-table-column>
        </a-table>
        </div>
      </a-tab-pane>

      <a-tab-pane key="checks" tab="自检">
        <div class="table-card">
        <div class="wrap-row mb-3">
          <a-button size="small" @click="load">刷新自检</a-button>
          <a-button size="small" @click="loadLog">查看日志尾部</a-button>
        </div>
        <a-table :columns="checkColumns" :data-source="checks" row-key="name" size="middle" :pagination="false">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'level'">
              <a-tag :color="LEVEL_META[record.level]?.color || 'default'">
                {{ LEVEL_META[record.level]?.text || record.level }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'name'">
              <code>{{ record.name }}</code>
            </template>
          </template>
        </a-table>
        <template v-if="logLines.length">
          <p class="log-title">日志尾部（最近 200 行）</p>
          <pre class="log-view">{{ logLines.join("") }}</pre>
        </template>
        </div>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<style scoped>
.status-row { margin-bottom: var(--space-4); }
.log-view { max-height: 320px; overflow: auto; background: #fafafa; padding: 12px; font-size: 12px; border-radius: 4px; margin: 0; }
.log-title { margin: var(--space-3) 0 var(--space-2); font-size: 13px; color: var(--c-sub); }
.mt-3 { margin-top: var(--space-3); }
.mb-3 { margin-bottom: var(--space-3); }
</style>
