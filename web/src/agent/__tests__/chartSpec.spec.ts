// web/src/agent/__tests__/chartSpec.spec.ts
import { describe, expect, it } from "vitest";
import { parseChartSpec } from "../chartSpec";

describe("parseChartSpec", () => {
  it("chart-pie：合法一维数据", () => {
    const r = parseChartSpec("chart-pie", '{"title":"构成","data":[{"name":"travel","value":12.5}]}');
    expect(r?.kind).toBe("pie");
    expect(r?.spec.title).toBe("构成");
    expect(r?.spec.data).toEqual([{ name: "travel", value: 12.5 }]);
  });

  it("chart-bar：合法一维数据", () => {
    const r = parseChartSpec("chart-bar", '{"data":[{"name":"a","value":1},{"name":"b","value":2}]}');
    expect(r?.kind).toBe("bar");
    expect(r?.spec.data).toHaveLength(2);
  });

  it("chart-grouped-bar：合法二维数据", () => {
    const raw = '{"categories":["travel","office"],"series":[{"name":"2026-09","values":[1,2]}]}';
    const r = parseChartSpec("chart-grouped-bar", raw);
    expect(r?.kind).toBe("grouped-bar");
    expect(r?.spec.categories).toEqual(["travel", "office"]);
    expect(r?.spec.series).toEqual([{ name: "2026-09", values: [1, 2] }]);
  });

  it("chart-radar：与 grouped-bar 同一种数据结构", () => {
    const raw = '{"categories":["a","b","c"],"series":[{"name":"s1","values":[1,2,3]}]}';
    const r = parseChartSpec("chart-radar", raw);
    expect(r?.kind).toBe("radar");
    expect(r?.spec.series?.[0].values).toEqual([1, 2, 3]);
  });

  it("非法 JSON → null", () => {
    expect(parseChartSpec("chart-pie", '{"data":[')).toBeNull();
    expect(parseChartSpec("chart-pie", "不是 JSON")).toBeNull();
  });

  it("空 data / 元素全非法 → null", () => {
    expect(parseChartSpec("chart-pie", '{"data":[]}')).toBeNull();
    expect(parseChartSpec("chart-pie", '{"data":[{"name":"a","value":"xx"},{"name":1,"value":2}]}')).toBeNull();
    expect(parseChartSpec("chart-bar", "{}")).toBeNull();
  });

  it("一维：非法元素丢弃，剩合法即通过", () => {
    const r = parseChartSpec("chart-bar", '{"data":[{"name":"a","value":"1.5"},{"name":"b","value":null}]}');
    expect(r?.spec.data).toEqual([{ name: "a", value: 1.5 }]);
  });

  it("字符串数字转 number", () => {
    const r = parseChartSpec("chart-pie", '{"data":[{"name":"a","value":"12345.67"}]}');
    expect(r?.spec.data?.[0].value).toBe(12345.67);
  });

  it("grouped-bar：values 长度与 categories 不符 → null", () => {
    const raw = '{"categories":["a","b"],"series":[{"name":"s","values":[1]}]}';
    expect(parseChartSpec("chart-grouped-bar", raw)).toBeNull();
  });

  it("grouped-bar：series 为空 / categories 非字符串数组 → null", () => {
    expect(parseChartSpec("chart-grouped-bar", '{"categories":["a"],"series":[]}')).toBeNull();
    expect(parseChartSpec("chart-radar", '{"categories":[1],"series":[{"name":"s","values":[1]}]}')).toBeNull();
  });

  it("不认识的 lang → null", () => {
    expect(parseChartSpec("chart-baz", '{"data":[{"name":"a","value":1}]}')).toBeNull();
    expect(parseChartSpec("json", '{"data":[{"name":"a","value":1}]}')).toBeNull();
    expect(parseChartSpec("", '{"data":[{"name":"a","value":1}]}')).toBeNull();
  });

  it("顶层非对象（数组/字符串）→ null", () => {
    expect(parseChartSpec("chart-pie", "[1,2]")).toBeNull();
    expect(parseChartSpec("chart-pie", '"x"')).toBeNull();
  });
});
