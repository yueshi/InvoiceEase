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

  it("提示词含 Skill 触发词（与 docs/skills 的 description 对齐）", () => {
    const w = mount(EntryCards);
    // 「出差」/「招待」/「交通」等是 5 份 SKILL.md 的触发词
    expect(w.text()).toContain("出差");
    expect(w.text()).toContain("招待");
    expect(w.text()).toContain("发票");
  });
});
