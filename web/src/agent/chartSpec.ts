// web/src/agent/chartSpec.ts
// chart-* fence 的 JSON 形状解析与校验（纯函数、无框架依赖）。
// fence 协议见后端 system_prompts.py【出图规则】，形状不合法一律返回 null，由调用方降级。

export type ChartKind = "pie" | "bar" | "grouped-bar" | "radar";

export interface ChartDataItem {
  name: string;
  value: number;
}

export interface ChartSpec {
  title?: string;
  data?: ChartDataItem[];
  categories?: string[];
  series?: { name: string; values: number[] }[];
}

const KINDS: ChartKind[] = ["pie", "bar", "grouped-bar", "radar"];

/** 可转数字 → number；否则 null（空串/布尔/非法字符串都算非法） */
function toNum(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** lang=chart-xxx 且 JSON 合法且形状通过校验 → {kind, spec}；否则 null */
export function parseChartSpec(lang: string, raw: string): { kind: ChartKind; spec: ChartSpec } | null {
  const kind = lang.startsWith("chart-") ? (lang.slice(6) as ChartKind) : null;
  if (!kind || !KINDS.includes(kind)) return null;

  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;
  const o = obj as Record<string, unknown>;

  const spec: ChartSpec = {};
  if (typeof o.title === "string") spec.title = o.title;

  // 一维：pie / bar —— data 非空，元素 name 为 string、value 可转数字（非法元素丢弃）
  if (kind === "pie" || kind === "bar") {
    if (!Array.isArray(o.data)) return null;
    const data: ChartDataItem[] = [];
    for (const it of o.data) {
      if (typeof it !== "object" || it === null) continue;
      const { name, value } = it as Record<string, unknown>;
      const n = toNum(value);
      if (typeof name !== "string" || !name || n === null) continue;
      data.push({ name, value: n });
    }
    if (!data.length) return null;
    spec.data = data;
    return { kind, spec };
  }

  // 二维：grouped-bar / radar —— categories 非空 string 数组 + series 非空，
  // 每项 values 长度必须等于 categories 长度（不符 → null）
  if (!Array.isArray(o.categories) || !o.categories.length) return null;
  if (!o.categories.every((c) => typeof c === "string" && c)) return null;
  if (!Array.isArray(o.series) || !o.series.length) return null;
  const categories = o.categories as string[];
  const series: { name: string; values: number[] }[] = [];
  for (const it of o.series) {
    if (typeof it !== "object" || it === null) return null;
    const { name, values } = it as Record<string, unknown>;
    if (typeof name !== "string") return null;
    if (!Array.isArray(values) || values.length !== categories.length) return null;
    const nums = values.map(toNum);
    if (nums.some((n) => n === null)) return null;
    series.push({ name, values: nums as number[] });
  }
  spec.categories = categories;
  spec.series = series;
  return { kind, spec };
}
