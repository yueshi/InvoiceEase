// web/src/agent/__tests__/markdown.spec.ts
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../markdown";

describe("renderMarkdown", () => {
  it("原始 HTML 一律转义（XSS 底线）", () => {
    const out = renderMarkdown("<script>alert(1)</script>");
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });

  it("javascript: 链接被拒（不产生 href）", () => {
    const out = renderMarkdown("[x](javascript:alert(1))");
    expect(out).not.toContain('href="javascript:');
    expect(renderMarkdown("[x](JaVaScRiPt:alert(1))")).not.toContain('href="javascript:'); // 大小写混杂同样拒
  });

  it("https 链接正常成链", () => {
    expect(renderMarkdown("[x](https://e.com)")).toContain('href="https://e.com"');
  });

  it("单换行成 <br>，粗体/列表按 markdown 渲染", () => {
    expect(renderMarkdown("a\nb")).toContain("<br>");
    expect(renderMarkdown("**粗**")).toContain("<strong>粗</strong>");
    expect(renderMarkdown("- 一\n- 二")).toContain("<li>一</li>");
  });
});
