import { api } from "./client";
import type { StatsOverviewOut } from "../types";

export async function fetchOverview(): Promise<StatsOverviewOut> {
  const { data } = await api.get<StatsOverviewOut>("/stats/overview");
  return data;
}
