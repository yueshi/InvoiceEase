import type { ReceiptOut } from "../types";

/** 金额显示 formatter：千分位 + 保留 2 位；空值/非法值显示占位符（后端 Decimal 以字符串下发） */
export function formatMoney(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * 回单状态文案/配色：配对与否以 paired_invoice_id 为门（与后端无票队列同一门，
 * 终审 1b——status 可能因删票 `ON DELETE SET NULL` 滞后成 "paired"）；
 * 未配对行按发票要求三分（fetch=需追发票 / issue=我方需开销项票 / none=无需发票）。
 * 列表与详情抽屉共用，避免两处各写一份口径（T5 审查：抽屉曾漏改，同一行两界面自相矛盾）。
 */
export function receiptStatusText(
  r: Pick<ReceiptOut, "status" | "invoice_requirement" | "paired_invoice_id">,
): string {
  if (r.paired_invoice_id !== null) return "已配对";
  if (r.status === "pending") return "待处理";
  if (r.invoice_requirement === "fetch") return "无票";
  if (r.invoice_requirement === "issue") return "待开票";
  return "无需发票";
}

export function receiptStatusColor(
  r: Pick<ReceiptOut, "status" | "invoice_requirement" | "paired_invoice_id">,
): string {
  if (r.paired_invoice_id !== null) return "green";
  if (r.status === "pending") return "blue";
  if (r.invoice_requirement === "fetch") return "orange";
  if (r.invoice_requirement === "issue") return "blue";
  return "default";
}
