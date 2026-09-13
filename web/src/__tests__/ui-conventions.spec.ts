// UI 约定防回归：errorMessage 自身弹提示，不得再包 message.error（会弹空消息）
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === "node_modules" ? [] : walk(p);
    return /\.(vue|ts)$/.test(p) ? [p] : [];
  });
}

describe("UI 约定", () => {
  it("不得出现 message.error(errorMessage(...)) 双重提示（空消息 bug）", () => {
    const files = walk(join(__dirname, "..")).filter((f) => !f.endsWith(".spec.ts"));
    const offenders = files.filter((f) => {
      // 剥离注释后再匹配：文档里举反例不算违规
      const src = readFileSync(f, "utf-8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/.*$/gm, "");
      return /message\.(error|warning|success|info)\(\s*errorMessage\(/.test(src);
    });
    expect(offenders).toEqual([]);
  });
});
