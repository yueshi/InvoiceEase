// web/src/agent/__tests__/markdown.spec.ts
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../markdown";

describe("renderMarkdown", () => {
  it("原始 HTML 一律转义（XSS 底线）", () => {
    const out = renderMarkdown("<script>alert(1)</script>");
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });

  it("javascript: 链接被拒（大小写无关：均不产生 href）", () => {
    const out = renderMarkdown("[x](javascript:alert(1))");
    expect(out).not.toContain('href="javascript:');
    // 大小写无关：混写协议同样拒，任何 href= 都不应出现（validateLink 即便大小写敏感也被抓）
    expect(renderMarkdown("[x](JaVaScRiPt:alert(1))")).not.toContain("href=");
  });

  it("https 链接正常成链", () => {
    expect(renderMarkdown("[x](https://e.com)")).toContain('href="https://e.com"');
  });

  it("单换行成 <br>，粗体/列表按 markdown 渲染", () => {
    expect(renderMarkdown("a\nb")).toContain("<br>");
    expect(renderMarkdown("**粗**")).toContain("<strong>粗</strong>");
    expect(renderMarkdown("- 一\n- 二")).toContain("<li>一</li>");
  });

  it("表格后无空行的正文不被吞成表格行（normalizeTables）", () => {
    const out = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |\n后续 **文字**");
    expect(out.match(/<tr>/g)?.length).toBe(2); // 表头 + 1 数据行（正文若被吞会变 3）
    expect(out).toMatch(/<\/table>[\s\S]*<strong>文字<\/strong>/);
  });

  it("边界：`---` 行/表格后接列表 不被规范化破坏", () => {
    // 无管道符的 --- 不是表格分隔行（setext 标题/分隔线场景），不得进入表格模式
    const hr = renderMarkdown("文字\n---\n文字2");
    expect(hr).not.toContain("<table>");
    expect(hr).toContain("文字2");
    // 表格后接列表（markdown-it 本就能识别列表；补空行后仍应正常）
    const out = renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |\n- 一\n- 二");
    expect(out).toMatch(/<\/table>[\s\S]*<li>一<\/li>/);
  });
});
