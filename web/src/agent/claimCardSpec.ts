// web/src/agent/claimCardSpec.ts
// 助手回复里的结构化卡片契约（P2 对话式报销漏斗）。
//
// 与 chartSpec 同策略：fence lang 认卡片类型 + JSON 形状校验。
// **解析失败一律返回 null** —— 组件回落为代码块，绝不把半截/脏数据
// 渲染成"看起来对"的卡片（流式未闭合时尤其常见）。
//
// 卡片只做展示与"注入预置消息"，不自己调 API 写库：
// 写操作仍走两段握手（点卡 ≠ 用户已确认，见 system_prompts 出卡规则）。

export interface DraftEntry {
  title?: string;
  amount?: string;
}

export interface DraftCard {
  claim_no?: string;
  title?: string;
  claim_type?: string;
  total_amount?: string;
  status?: string;
  entries: DraftEntry[];
}

export interface AnomalyOption {
  label: string;
  prompt: string;
}

export interface AnomalyCard {
  kind?: string;
  message: string;
  options: AnomalyOption[];
}

/** 非空字符串才算有效值（空串/纯空白视为缺失，避免渲染出空行） */
const str = (v: unknown): string | undefined =>
  typeof v === "string" && v.trim() ? v : undefined;

function parseJson(raw: string): Record<string, unknown> | null {
  try {
    const obj = JSON.parse(raw);
    if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;
    return obj as Record<string, unknown>;
  } catch {
    return null; // 半截 JSON（流式未闭合）走这里
  }
}

export function parseDraftCard(lang: string, raw: string): DraftCard | null {
  if (lang !== "expense-draft") return null;
  const o = parseJson(raw);
  if (!o) return null;
  const claim_no = str(o.claim_no);
  const title = str(o.title);
  const total_amount = str(o.total_amount);
  const entries: DraftEntry[] = Array.isArray(o.entries)
    ? (o.entries as unknown[]).flatMap((it) => {
        if (typeof it !== "object" || it === null) return [];
        const e = it as Record<string, unknown>;
        const title = str(e.title);
        const amount = str(e.amount);
        return title || amount ? [{ title, amount }] : [];
      })
    : [];
  // 空壳卡（模型给了个 {} 或字段全不认识）不渲染：否则用户看到"报销单预览/[确认提交]"
  // 却不知道是哪张单，点下去发出的指令无指代。宁可回落代码块。
  if (!claim_no && !title && !total_amount && !entries.length) return null;
  return {
    claim_no, title, claim_type: str(o.claim_type),
    total_amount, status: str(o.status), entries,
  };
}

export function parseAnomalyCard(lang: string, raw: string): AnomalyCard | null {
  if (lang !== "anomaly") return null;
  const o = parseJson(raw);
  if (!o) return null;
  const message = str(o.message);
  if (!message) return null; // 没有原因的异常卡是废卡
  const options = (Array.isArray(o.options) ? (o.options as unknown[]) : [])
    .flatMap((it) => {
      if (typeof it !== "object" || it === null) return [];
      const x = it as Record<string, unknown>;
      const label = str(x.label);
      const prompt = str(x.prompt);
      return label && prompt ? [{ label, prompt }] : [];
    })
    .slice(0, 4);
  return { kind: str(o.kind), message, options };
}
