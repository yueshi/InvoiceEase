// web/src/agent/markdown.ts
// 助手回复的 markdown 渲染入口。
// html:false —— 原始 HTML 一律转义（XSS 底线：工具返回值可能带第三方文本，如卖方名称）
// linkify —— 裸 URL 自动成链；breaks —— 单换行成 <br>（贴聊天习惯）
import MarkdownIt from "markdown-it";

export const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

export function renderMarkdown(text: string): string {
  return md.render(text);
}
