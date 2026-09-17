<!-- 运维（兜底，admin）：状态卡片 + 任务/告警/备份/自检四 Tab（设计 §10） -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
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

const status = ref<OpsStatusOut | null>(null);
const runs = ref<TaskRunOut[]>([]);
const runsTotal = ref(0);
const alerts = ref<OpsAlertOut[]>([]);
const backups = ref<BackupOut[]>([]);
const logLines = ref<string[]>([]);
const activeTab = ref("tasks");
const busy = ref(false);

const RUNNABLE = ["mailbox_poll", "review_predict", "monthly_health", "audit_retention", "ops_check", "ops_backup"];

const OUTCOME_META: Record<string, { text: string; color: string }> = {
  success: { text: "成功", color: "green" },
  failed: { text: "失败", color: "orange" },
  error: { text: "异常", color: "red" },
  missed: { text: "错过", color: "default" },
};
const LEVEL_META: Record<string, { color: string }> = {
  ok: { color: "green" },
  warn: { color: "orange" },
  fail: { color: "red" },
  info: { color: "blue" },
};

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
  try {
    const [s, r, a, b] = await Promise.all([
      fetchOpsStatus(),
      listTaskRuns(),
      listOpsAlerts(),
      listBackups(),
    ]);
    status.value = s;
    runs.value = r.items;
    runsTotal.value = r.total;
    alerts.value = a.items;
    backups.value = b;
  } catch (e) {
    errorMessage(e, "运维状态加载失败");
  }
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
    <h3>运维</h3>
    <p style="color: #888; margin-bottom: 12px">系统状态、任务班表执行记录、告警历史、备份与自检（仅管理员）。</p>

    <a-row v-if="status" :gutter="12" style="margin-bottom: 16px">
      <a-col :span="4"><a-card size="small"><a-statistic title="版本" :value="status.version" /></a-card></a-col>
      <a-col :span="4"><a-card size="small"><a-statistic title="运行时长" :value="fmtUptime(status.uptime_seconds)" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :span="4"><a-card size="small"><a-statistic title="数据库" :value="fmtBytes(status.storage.db_bytes)" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :span="4"><a-card size="small"><a-statistic title="原件体积" :value="fmtBytes(status.storage.originals_bytes)" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :span="4"><a-card size="small"><a-statistic title="磁盘剩余" :value="`${status.storage.disk_free_percent}%`" :value-style="{ fontSize: '20px' }" /></a-card></a-col>
      <a-col :span="4">
        <a-card size="small">
          <a-statistic title="最近备份" :value="status.last_backup ? fmtBackupTime(status.last_backup.name) : '无'"
                       :value-style="{ fontSize: '16px' }" />
        </a-card>
      </a-col>
    </a-row>

    <a-tabs v-model:activeKey="activeTab">
      <a-tab-pane key="tasks" tab="任务">
        <a-space style="margin-bottom: 12px" wrap>
          <a-button v-for="name in RUNNABLE" :key="name" size="small" :disabled="busy" @click="onRunTask(name)">
            手动执行 {{ name }}
          </a-button>
          <a-button size="small" @click="load">刷新</a-button>
        </a-space>
        <a-table :data-source="runs" :pagination="{ total: runsTotal, pageSize: 20 }" row-key="id" size="middle">
          <a-table-column title="任务" data-index="task_name" :width="140" />
          <a-table-column title="触发" data-index="trigger" :width="90" />
          <a-table-column title="开始时间" data-index="started_at" :width="170" />
          <a-table-column title="耗时(ms)" data-index="duration_ms" :width="90" />
          <a-table-column title="结果" key="outcome" :width="80">
            <template #bodyCell="{ record }">
              <a-tag :color="OUTCOME_META[record.outcome]?.color || 'default'">
                {{ OUTCOME_META[record.outcome]?.text || record.outcome }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="错误" data-index="error" ellipsis />
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="alerts" tab="告警">
        <a-table :data-source="alerts" :pagination="{ pageSize: 20 }" row-key="id" size="middle">
          <a-table-column title="级别" key="severity" :width="90">
            <template #bodyCell="{ record }">
              <a-tag :color="record.severity === 'critical' ? 'red' : 'orange'">
                {{ record.severity === "critical" ? "严重" : "警告" }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="规则" data-index="rule_key" :width="180" />
          <a-table-column title="内容" data-index="message" />
          <a-table-column title="触发时间" data-index="fired_at" :width="170" />
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="backups" tab="备份">
        <a-space style="margin-bottom: 12px">
          <a-button type="primary" :disabled="busy" @click="onBackup">立即备份</a-button>
        </a-space>
        <a-table :data-source="backups" :pagination="{ pageSize: 20 }" row-key="name" size="middle">
          <a-table-column title="文件" data-index="name" />
          <a-table-column title="大小" key="size" :width="110">
            <template #bodyCell="{ record }">{{ fmtBytes(record.size_bytes) }}</template>
          </a-table-column>
          <a-table-column title="时间" data-index="created_at" :width="170" />
          <a-table-column title="操作" key="act" :width="90">
            <template #bodyCell="{ record }">
              <a-button type="link" size="small" @click="downloadBackup(record.name)">下载</a-button>
            </template>
          </a-table-column>
        </a-table>
      </a-tab-pane>

      <a-tab-pane key="checks" tab="自检">
        <a-list :data-source="(status?.checks || []) as OpsCheckOut[]" size="small" bordered>
          <template #renderItem="{ item }">
            <a-list-item>
              <a-tag :color="LEVEL_META[item.level]?.color || 'default'">{{ item.level }}</a-tag>
              <b style="margin: 0 8px">{{ item.name }}</b>
              <span>{{ item.message }}</span>
            </a-list-item>
          </template>
        </a-list>
        <a-space style="margin-top: 12px">
          <a-button size="small" @click="load">刷新自检</a-button>
          <a-button size="small" @click="loadLog">查看日志尾部</a-button>
        </a-space>
        <pre v-if="logLines.length" style="max-height: 320px; overflow: auto; background: #fafafa; padding: 12px; font-size: 12px">{{ logLines.join("") }}</pre>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>
