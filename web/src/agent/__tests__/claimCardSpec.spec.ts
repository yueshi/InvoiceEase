// 结构化卡片解析器测试（P2 对话式报销漏斗）
// 契约：fence lang + JSON 形状校验；任何非法/半截输入一律 null → 组件回落代码块
import { describe, expect, it } from "vitest";
import { parseAnomalyCard, parseDraftCard } from "../claimCardSpec";

describe("parseDraftCard", () => {
  it("解析合法草稿卡", () => {
    const raw = JSON.stringify({
      claim_no: "FY-202610-0007", title: "上海出差", claim_type: "travel",
      total_amount: "1234.56", status: "draft",
      entries: [{ title: "高铁", amount: "553.00" }, { title: "住宿", amount: "681.56" }],
    });
    const card = parseDraftCard("expense-draft", raw);
    expect(card?.claim_no).toBe("FY-202610-0007");
    expect(card?.entries).toHaveLength(2);
    expect(card?.total_amount).toBe("1234.56");
    expect(card?.entries[0].title).toBe("高铁");
  });

  it("非目标 lang 返回 null", () => {
    expect(parseDraftCard("chart-pie", "{}")).toBeNull();
    expect(parseDraftCard("anomaly", '{"message":"m"}')).toBeNull();
  });

  it("半截 JSON 返回 null（流式未闭合）", () => {
    expect(parseDraftCard("expense-draft", '{"claim_no": "FY-1"')).toBeNull();
  });

  it("entries 非数组时按空处理，不抛", () => {
    const card = parseDraftCard("expense-draft",
      JSON.stringify({ title: "x", entries: "不是数组" }));
    expect(card?.entries).toEqual([]);
  });

  it("entries 里的脏元素被丢弃", () => {
    const card = parseDraftCard("expense-draft", JSON.stringify({
      entries: [null, "字符串", { title: "有效", amount: "1.00" }, {}],
    }));
    expect(card?.entries).toEqual([{ title: "有效", amount: "1.00" }]);
  });

  it("金额保留原样字符串（不加工不猜）", () => {
    const card = parseDraftCard("expense-draft",
      JSON.stringify({ total_amount: "1,234.56" }));
    expect(card?.total_amount).toBe("1,234.56");
  });

  it("空对象不崩（返回空卡，由组件决定怎么显示）", () => {
    const card = parseDraftCard("expense-draft", "{}");
    expect(card).not.toBeNull();
    expect(card?.entries).toEqual([]);
  });
});

describe("parseAnomalyCard", () => {
  it("解析异常卡与选项", () => {
    const raw = JSON.stringify({
      kind: "OVER_STANDARD", message: "招待人均 450 元超标准 300 元",
      options: [
        { label: "补充材料", prompt: "我要补充材料" },
        { label: "申请特批", prompt: "帮我申请特批" },
      ],
    });
    const card = parseAnomalyCard("anomaly", raw);
    expect(card?.message).toContain("超标准");
    expect(card?.kind).toBe("OVER_STANDARD");
    expect(card?.options.map((o) => o.label)).toEqual(["补充材料", "申请特批"]);
  });

  it("无 message 返回 null（卡片没有内容是废卡）", () => {
    expect(parseAnomalyCard("anomaly", JSON.stringify({ options: [] }))).toBeNull();
    expect(parseAnomalyCard("anomaly", JSON.stringify({ message: "   " }))).toBeNull();
  });

  it("选项缺 prompt 时丢弃该选项", () => {
    const card = parseAnomalyCard("anomaly", JSON.stringify({
      message: "m", options: [{ label: "A" }, { label: "B", prompt: "b" }],
    }));
    expect(card?.options).toEqual([{ label: "B", prompt: "b" }]);
  });

  it("选项超过 4 个时截断（界面只放得下四个）", () => {
    const card = parseAnomalyCard("anomaly", JSON.stringify({
      message: "m",
      options: Array.from({ length: 6 }, (_, i) => ({ label: `L${i}`, prompt: `p${i}` })),
    }));
    expect(card?.options).toHaveLength(4);
  });

  it("半截 JSON 返回 null", () => {
    expect(parseAnomalyCard("anomaly", '{"message": "m", "options": [')).toBeNull();
  });
});
