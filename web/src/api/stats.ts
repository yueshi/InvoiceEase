import { api } from "./client";
import type { StatsOverviewOut, TrustStatsOut } from "../types";

export async function fetchOverview(): Promise<StatsOverviewOut> {
  const { data } = await api.get<StatsOverviewOut>("/stats/overview");
  return data;
}

export async function fetchTrustStats(days = 7): Promise<TrustStatsOut> {
  const { data } = await api.get<TrustStatsOut>("/stats/trust", { params: { days } });
  return data;
}
