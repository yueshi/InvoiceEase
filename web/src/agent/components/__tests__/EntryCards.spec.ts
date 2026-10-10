// 领域入口卡测试（P2 §6.2：报销发起对话的 5 个入口）
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import EntryCards from "../EntryCards.vue";

describe("EntryCards", () => {
  it("渲染 5 个领域入口", () => {
    const w = mount(EntryCards);
    const items = w.findAll("button.ec-item");
    expect(items).toHaveLength(5);
    for (const label of ["出差报销", "吃饭/招待", "打车/高铁", "补充发票", "超标怎么办"]) {
      expect(w.text()).toContain(label);
    }
  });

  it("点入口 → emit pick(该入口的预置提示词)", async () => {
    const w = mount(EntryCards);
    await w.findAll("button.ec-item")[0].trigger("click");
    expect(w.emitted("pick")?.[0]).toEqual(["我要报出差费用"]);
  });

  it("5 个提示词互不相同且非空（防复制粘贴漏改）", async () => {
    const w = mount(EntryCards);
    const prompts: string[] = [];
    for (const b of w.findAll("button.ec-item")) {
      await b.trigger("click");
      prompts.push((w.emitted("pick")![prompts.length] as [string])[0]);
    }
    expect(new Set(prompts).size).toBe(5);
    expect(prompts.every((p) => p.trim().length > 0)).toBe(true);
  });

  it("**发出的提示词**含对应 Skill 的触发关键词（不是断言 label）", async () => {
    // 关键词取自 docs/skills/*/SKILL.md 的 description 触发词：
    // 平台按 description 语义路由，用户消息与之共享词汇才稳。
    const KEYWORDS: Record<string, string[]> = {
      travel: ["出差", "差旅"],
      meal: ["餐饮", "招待", "吃饭"],
      transport: ["打车", "高铁", "交通"],
      supplement: ["补充", "补录"],
      "over-budget": ["超标", "超预算"],
    };
    const keys = ["travel", "meal", "transport", "supplement", "over-budget"];
    const w = mount(EntryCards);
    const buttons = w.findAll("button.ec-item");
    expect(buttons).toHaveLength(keys.length);
    for (let i = 0; i < keys.length; i++) {
      await buttons[i].trigger("click");
      const [prompt] = w.emitted("pick")![i] as [string];
      const words = KEYWORDS[keys[i]];
      expect(
        words.some((k) => prompt.includes(k)),
        `${keys[i]} 的提示词「${prompt}」不含任何触发关键词 ${words}`,
      ).toBe(true);
    }
  });
});
