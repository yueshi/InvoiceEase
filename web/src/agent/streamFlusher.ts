// web/src/agent/streamFlusher.ts
// 流式 markdown「安全点 flush」策略（移植自 AuditMind 的同名思路，纯字符串逻辑、无框架依赖）。
// 目的：token 逐条到达时不直接整段重解析，只在「安全点」把已完整的一段交给 markdown 渲染，
// 尾部处于未闭合行内语法（`**` / ` / [`）时回退，避免半开语法在屏幕上反复重排/闪烁。

export interface Segment {
  kind: "md" | "fence";
  lang: string;
  content: string;
  closed: boolean;
}

/** 水位：没有任何边界时攒够 200 字整体输出，保证长句/长表格也有反馈 */
const WATERMARK = 200;
/** 段落边界 */
const PARA = "\n\n";
/** 中文句末（含紧随的换行或空格） */
const SENTENCE_ENDS = ["。\n", "。 ", "！\n", "？\n"];
/** fence 行：行首（可缩进）的 ≥3 个反引号；组 1 是反引号串（长度即 fence 长度），组 2 是 info string */
const FENCE_LINE = /^[ \t]*(`{3,})(.*)$/gm;

/** 非重叠统计 token 出现次数 */
function countAll(s: string, token: string): number {
  let n = 0;
  for (let i = s.indexOf(token); i !== -1; i = s.indexOf(token, i + token.length)) n++;
  return n;
}

/** 命中所有「安全点」的末尾下标（去重、降序）——flushablePrefix 按序回退用 */
function boundaryPoints(text: string): number[] {
  const pts: number[] = [];
  for (const b of [PARA, ...SENTENCE_ENDS]) {
    for (let i = text.indexOf(b); i !== -1; i = text.indexOf(b, i + 1)) pts.push(i + b.length);
  }
  return [...new Set(pts)].sort((a, b) => b - a);
}

/**
 * 按 ``` fence 切段：fence 开始行 ```` ```lang ```` 的 lang 取 info string 首个词；
 * 闭合遵循 CommonMark——闭合行反引号数必须 ≥ 开启数，更短的反引号行按内容处理；
 * 文本中未闭合的尾部 fence → closed:false；fence 之外为 md 段（空段省略）。
 */
export function splitSegments(text: string): Segment[] {
  const segs: Segment[] = [];
  const pushMd = (content: string) => {
    if (content) segs.push({ kind: "md", lang: "", content, closed: true });
  };
  const pushFence = (lang: string, content: string, closed: boolean) =>
    segs.push({ kind: "fence", lang, content, closed });

  const re = new RegExp(FENCE_LINE.source, "gm"); // 每次新建正则，避免 lastIndex 残留
  let mdStart = 0;
  let open: { lang: string; ticks: number; bodyStart: number } | null = null;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const afterLine = m.index + m[0].length + 1; // 跳过 fence 行末尾换行
    if (open) {
      if (m[1].length < open.ticks) continue; // 反引号更短 → 不是闭合行，按内容处理
      // 闭合 fence：剥掉正文末尾那个换行（它是 fence 行的分隔符，不是内容）
      pushFence(open.lang, text.slice(open.bodyStart, m.index).replace(/\n$/, ""), true);
      open = null;
      mdStart = afterLine;
    } else {
      pushMd(text.slice(mdStart, m.index));
      open = { lang: (m[2].trim().split(/\s+/)[0] ?? ""), ticks: m[1].length, bodyStart: afterLine };
    }
  }
  if (open) pushFence(open.lang, text.slice(open.bodyStart), false);
  else pushMd(text.slice(mdStart));
  return segs;
}

/** 尾部是否处于「未闭合行内语法」：`**` 奇数 / 单反引号奇数 / `[` 无配对 `]` */
export function isInsideOpenInline(s: string): boolean {
  if (!s) return false;
  if (countAll(s, "`") % 2 === 1) return true;
  if (countAll(s, "**") % 2 === 1) return true;
  const open = s.lastIndexOf("[");
  if (open !== -1 && !s.slice(open).includes("]")) return true;
  return false;
}

/**
 * 返回可安全渲染为 markdown 的前缀（其余部分由调用方按纯文本展示，防止半开语法闪烁）。
 * 安全点优先级：最后一个 "\n\n"（含）→ 中文句末（含）→ 长度 ≥200 则整体 → 否则 ""。
 * 再防护：前缀尾部处于未闭合行内语法时，回退到上一安全点（循环直到安全或空）。
 */
export function flushablePrefix(text: string): string {
  if (!text) return "";
  const pts = boundaryPoints(text);
  const candidates = pts.length ? pts : text.length >= WATERMARK ? [text.length] : [];
  for (const p of [...candidates, 0]) {
    const prefix = text.slice(0, p);
    if (!isInsideOpenInline(prefix)) return prefix;
  }
  return "";
}
