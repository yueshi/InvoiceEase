// 运维兜底 API（/api/v1/ops，admin）
import { api, downloadFile } from "./client";
import type {
  BackupOut,
  OpsAlertListResponse,
  OpsStatusOut,
  TaskRunListResponse,
} from "../types";

export async function fetchOpsStatus(): Promise<OpsStatusOut> {
  const { data } = await api.get<OpsStatusOut>("/ops/status");
  return data;
}

export async function listTaskRuns(params?: {
  task_name?: string;
  outcome?: string;
  page?: number;
}): Promise<TaskRunListResponse> {
  const { data } = await api.get<TaskRunListResponse>("/ops/tasks", { params });
  return data;
}

export async function runTask(name: string): Promise<void> {
  // 手动任务同步执行（含备份可达数分钟），放宽到 600s 超时
  await api.post(`/ops/tasks/${name}/run`, undefined, { timeout: 600000 });
}

export async function listOpsAlerts(params?: {
  severity?: string;
  page?: number;
}): Promise<OpsAlertListResponse> {
  const { data } = await api.get<OpsAlertListResponse>("/ops/alerts", { params });
  return data;
}

export async function listBackups(): Promise<BackupOut[]> {
  const { data } = await api.get<BackupOut[]>("/ops/backups");
  return data;
}

export async function runBackup(): Promise<{ name: string }> {
  // 备份同步执行（打包原件+轮转可达分钟级），放宽到 600s 超时
  const { data } = await api.post<{ name: string }>("/ops/backups/run", undefined, {
    timeout: 600000,
  });
  return data;
}

export function downloadBackup(name: string): void {
  downloadFile(`/ops/backups/${name}/download`, name);
}

export async function fetchLogTail(lines = 200): Promise<string[]> {
  const { data } = await api.get<{ lines: string[] }>("/ops/logs/tail", { params: { lines } });
  return data.lines;
}
