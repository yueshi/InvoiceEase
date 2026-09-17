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
  await api.post(`/ops/tasks/${name}/run`);
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
  const { data } = await api.post<{ name: string }>("/ops/backups/run");
  return data;
}

export function downloadBackup(name: string): void {
  downloadFile(`/ops/backups/${name}/download`, name);
}

export async function fetchLogTail(lines = 200): Promise<string[]> {
  const { data } = await api.get<{ lines: string[] }>("/ops/logs/tail", { params: { lines } });
  return data.lines;
}
