// web/src/agent/markdown.ts
// 助手回复的 markdown 渲染入口。
// html:false —— 原始 HTML 一律转义（XSS 底线：工具返回值可能带第三方文本，如卖方名称）
// linkify —— 裸 URL 自动成链；breaks —— 单换行成 <br>（贴聊天习惯）
import MarkdownIt from "markdown-it";

export const md = new MarkdownIt({ html: false, breaks: true, linkify: true });

/** GFM 表格分隔行（| --- | :---: |）——必须含 `|`，避免把 `---`（分隔线/setext）误判。 */
const TABLE_DELIMITER_RE = /^[\s|:-]+$/;

function isTableDelimiter(line: string): boolean {
  return line.includes("-") && line.includes("|") && TABLE_DELIMITER_RE.test(line);
}

/**
 * 模型常在表格后省略空行直接接正文——markdown-it 会把这段正文**吞成表格行**
 * （渲染出多一格空 <td>），即「表格后内容未正常渲染」的根因（实测复现）。
 * 规范化：表格期间（分隔行之后）遇到「非 `|` 开头且非空」的行时，先补一个空行，
 * 让它回到正常段落解析。真实表格行都以 `|` 开头，不受影响。
 */
export function normalizeTables(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let inTable = false;
  let prevHasPipe = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (inTable) {
      if (trimmed === "") {
        inTable = false;
      } else if (!trimmed.startsWith("|")) {
        out.push(""); // 表格体后紧跟非表格行：补空行，防被吞
        inTable = false;
      }
    } else if (prevHasPipe && isTableDelimiter(trimmed)) {
      inTable = true; // 表头行 + 分隔行 → 进入表格
    }
    out.push(line);
    prevHasPipe = trimmed.includes("|");
  }
  return out.join("\n");
}

export function renderMarkdown(text: string): string {
  return md.render(normalizeTables(text));
}
