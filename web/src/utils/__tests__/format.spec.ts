import { describe, expect, it } from "vitest";
import { formatMoney } from "../format";

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
