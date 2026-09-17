import { describe, expect, it } from "vitest";
import type { ReceiptOut } from "../../types";
import { formatMoney, receiptStatusColor, receiptStatusText } from "../format";

/** 状态列入参只需三字段：配对门 + 发票要求 */
function row(o: Partial<Pick<ReceiptOut, "status" | "invoice_requirement" | "paired_invoice_id">>) {
  return { status: "unmatched", invoice_requirement: "fetch", paired_invoice_id: null, ...o } as Pick<
    ReceiptOut,
    "status" | "invoice_requirement" | "paired_invoice_id"
  >;
}

describe("formatMoney", () => {
  it("千分位 + 两位小数（数字与字符串入参）", () => {
    expect(formatMoney(1234.5)).toBe("1,234.50");
    expect(formatMoney("1234567.8")).toBe("1,234,567.80");
  });
  it("0 与负数正常格式化", () => {
    expect(formatMoney(0)).toBe("0.00");
    expect(formatMoney("-88.4")).toBe("-88.40");
  });
  it("空值与非法值显示占位符 —", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney("")).toBe("—");
    expect(formatMoney("abc")).toBe("—");
  });
});

describe("receiptStatusText/Color：配对门 = paired_invoice_id", () => {
  it("status 滞后（paired 但未配对）不得显示「已配对」——与后端催票队列同一门", () => {
    const stale = row({ status: "paired", paired_invoice_id: null });
    expect(receiptStatusText(stale)).not.toBe("已配对");
    expect(receiptStatusColor(stale)).not.toBe("green");
  });

  it("paired_invoice_id 有值 → 已配对（绿）", () => {
    const paired = row({ status: "paired", paired_invoice_id: 7 });
    expect(receiptStatusText(paired)).toBe("已配对");
    expect(receiptStatusColor(paired)).toBe("green");
  });

  it("未配对行走既有三分（文案与配色不变）", () => {
    expect(receiptStatusText(row({ invoice_requirement: "fetch" }))).toBe("无票");
    expect(receiptStatusColor(row({ invoice_requirement: "fetch" }))).toBe("orange");
    expect(receiptStatusText(row({ invoice_requirement: "issue" }))).toBe("待开票");
    expect(receiptStatusText(row({ invoice_requirement: "none" }))).toBe("无需发票");
  });
});
