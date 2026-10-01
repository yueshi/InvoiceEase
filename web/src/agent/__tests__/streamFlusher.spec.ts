// web/src/agent/__tests__/streamFlusher.spec.ts
import { describe, expect, it } from "vitest";
import { flushablePrefix, isInsideOpenInline, splitSegments } from "../streamFlusher";

describe("flushablePrefix", () => {
  it("空串 → 空", () => {
    expect(flushablePrefix("")).toBe("");
  });

  it("段落边界优先：flush 到最后一个 \\n\\n（含）", () => {
    expect(flushablePrefix("第一段\n\n第二段还没写完")).toBe("第一段\n\n");
  });

  it("无段落边界时到中文句末（含标点与空格）", () => {
    expect(flushablePrefix("这句话说完了。 下一句还在写")).toBe("这句话说完了。 ");
    expect(flushablePrefix("强调！\n后面继续")).toBe("强调！\n");
  });

  it("水位：无任何边界且 ≥200 字 → 整体", () => {
    const long = "字".repeat(250);
    expect(flushablePrefix(long)).toBe(long);
  });

  it("不足水位又无边界 → 不 flush", () => {
    expect(flushablePrefix("还没写完的半句")).toBe("");
  });

  it("未闭合 ** → 回退到上一安全点", () => {
    expect(flushablePrefix("开头。 \n\n中间 **加粗未闭合\n\n尾巴")).toBe("开头。 \n\n");
  });

  it("未闭合行内语法之前没有安全点 → 空", () => {
    expect(flushablePrefix("开头 **加粗\n\n尾巴")).toBe("");
  });

  it("已闭合的 ** 不回退", () => {
    const s = "**加粗** 后面正常\n\n尾巴";
    expect(flushablePrefix(s)).toBe("**加粗** 后面正常\n\n");
  });
});

describe("isInsideOpenInline", () => {
  it("空串 → false", () => {
    expect(isInsideOpenInline("")).toBe(false);
  });

  it("** 奇数是开放（未闭合）", () => {
    expect(isInsideOpenInline("这个**重要")).toBe(true);
    expect(isInsideOpenInline("**重要**你看")).toBe(false);
  });

  it("单反引号奇数是开放", () => {
    expect(isInsideOpenInline("试试 `foo")).toBe(true);
    expect(isInsideOpenInline("试试 `foo` 好")).toBe(false);
  });

  it("[ 无配对 ] 是开放", () => {
    expect(isInsideOpenInline("看这里 [文档")).toBe(true);
    expect(isInsideOpenInline("看这里 [文档] 好")).toBe(false);
  });
});

describe("splitSegments", () => {
  it("无 fence → 全 md 段", () => {
    const segs = splitSegments("纯文本\n第二行");
    expect(segs).toEqual([{ kind: "md", lang: "", content: "纯文本\n第二行", closed: true }]);
  });

  it("空串 → 无段（空段省略）", () => {
    expect(splitSegments("")).toEqual([]);
  });

  it("单 fence：md + fence，lang 取 info string 首个词", () => {
    const segs = splitSegments('前面\n```chart-pie\n{"data":[]}\n```\n后面');
    expect(segs.map((s) => s.kind)).toEqual(["md", "fence", "md"]);
    expect(segs[0].content).toBe("前面\n");
    expect(segs[1]).toMatchObject({ lang: "chart-pie", content: '{"data":[]}', closed: true });
    expect(segs[2].content).toBe("后面");
  });

  it("未闭合的尾 fence → closed:false，content 为剩余全部", () => {
    const segs = splitSegments("```chart-bar 多余说明\n{\"data\":[1");
    expect(segs).toHaveLength(1);
    expect(segs[0]).toMatchObject({ kind: "fence", lang: "chart-bar", closed: false });
    expect(segs[0].content).toBe('{"data":[1');
  });

  it("多 fence 交错", () => {
    const segs = splitSegments("A\n```json\n{}\n```\nB\n```\ncode\n```\nC");
    expect(segs.map((s) => s.kind)).toEqual(["md", "fence", "md", "fence", "md"]);
    expect(segs.map((s) => s.lang)).toEqual(["", "json", "", "", ""]);
    expect(segs[3].content).toBe("code");
  });

  it("``` 无 lang → lang 为空串", () => {
    expect(splitSegments("```\nx\n```")[0]).toMatchObject({ lang: "", closed: true, content: "x" });
  });
});
