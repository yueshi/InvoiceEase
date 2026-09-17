// src/views/__tests__/ops.spec.ts
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OpsView from "../OpsView.vue";

/**
 * 自检结果在 a-list 的 #renderItem 作用域插槽里渲染——未注册 Antd 时插槽不执行、
 * props 不进 DOM（仓库已知坑 #1）。故按 mcp-tokens.spec.ts 既有模式全局注册 Antd，
 * 并 mock matchMedia（jsdom 无此 API，坑 #2）。
 */
async function mountWithAntd() {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false, media: query, onchange: null,
        addListener: () => {}, removeListener: () => {},
        addEventListener: () => {}, removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
  const Antd = (await import("ant-design-vue")).default;
  return mount(OpsView, { global: { plugins: [Antd] } });
}

/** antd Tabs 默认懒渲染：未激活的 tab 内容不进 DOM，先切到「自检」再断言 */
async function activateChecksTab(wrapper: ReturnType<typeof mount>) {
  const tab = wrapper.findAll(".ant-tabs-tab").find((t) => t.text().includes("自检"));
  expect(tab).toBeTruthy();
  await tab!.trigger("click");
  await new Promise((r) => setTimeout(r, 0));
}

/** 切到任意 Tab（同懒渲染原因） */
async function activateTab(wrapper: ReturnType<typeof mount>, label: string) {
  const tab = wrapper.findAll(".ant-tabs-tab").find((t) => t.text().includes(label));
  expect(tab).toBeTruthy();
  await tab!.trigger("click");
  await new Promise((r) => setTimeout(r, 0));
}

const fetchOpsStatus = vi.fn().mockResolvedValue({
  version: "0.1.0",
  uptime_seconds: 3600,
  checks: [
    { name: "fernet_key", level: "warn", message: "fernet_key 非法（需 32 字节 base64）" },
    { name: "disk_free", level: "ok", message: "磁盘剩余 62.0%" },
  ],
  metrics: { last_24h: { created: 3, verify_passed: 2, pending_review: 1, blocked: 0 }, review_backlog: 1 },
  storage: { db_bytes: 1024, originals_bytes: 2048, disk_free_percent: 62.0 },
  last_backup: null,
});
const listTaskRuns = vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
const listOpsAlerts = vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
const listBackups = vi.fn().mockResolvedValue([]);

vi.mock("../../api/ops", () => ({
  fetchOpsStatus: (...a: unknown[]) => fetchOpsStatus(...a),
  listTaskRuns: (...a: unknown[]) => listTaskRuns(...a),
  listOpsAlerts: (...a: unknown[]) => listOpsAlerts(...a),
  listBackups: (...a: unknown[]) => listBackups(...a),
  runTask: vi.fn(),
  runBackup: vi.fn(),
  downloadBackup: vi.fn(),
  fetchLogTail: vi.fn(),
}));

describe("OpsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("渲染状态卡片与自检结果", async () => {
    const wrapper = await mountWithAntd();
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.text()).toContain("运维");
    await activateChecksTab(wrapper);
    expect(wrapper.text()).toContain("fernet_key");
    expect(fetchOpsStatus).toHaveBeenCalled();
    expect(listTaskRuns).toHaveBeenCalled();
    expect(listOpsAlerts).toHaveBeenCalled();
    expect(listBackups).toHaveBeenCalled();
  }, 20000);

  it("warn 级自检项可见（非 ok 项着色）", async () => {
    const wrapper = await mountWithAntd();
    await new Promise((r) => setTimeout(r, 0));
    await activateChecksTab(wrapper);
    expect(wrapper.html()).toContain("warn");
  }, 20000);

  it("自定义单元格列真实渲染（结果/级别/备份操作）——列插槽回归", async () => {
    // 回归：a-table-column 内曾误用 #bodyCell（该插槽只在 a-table 上生效），
    // 三个自定义列静默渲染空白——告警的「级别」、任务的「结果」、备份的「下载」全丢
    listTaskRuns.mockResolvedValueOnce({
      items: [{
        id: 1, task_name: "mailbox_poll", trigger: "scheduler",
        started_at: "2026-09-17T10:08:15", duration_ms: 6, outcome: "success", error: null,
      }],
      total: 1, page: 1, page_size: 20,
    });
    listOpsAlerts.mockResolvedValueOnce({
      items: [{
        id: 1, rule_key: "backup.missing", severity: "warning",
        message: "超过 26 小时无新备份", fired_at: "2026-09-17T09:59:15",
      }],
      total: 1, page: 1, page_size: 20,
    });
    listBackups.mockResolvedValueOnce([
      { name: "invoiceease-backup-20260917-021700.tar.gz", size_bytes: 2048, created_at: "2026-09-17T02:17:00" },
    ]);
    const wrapper = await mountWithAntd();
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.html()).toContain("成功"); // 任务表「结果」列 outcome → 中文标签
    await activateTab(wrapper, "告警");
    expect(wrapper.html()).toContain("警告"); // 告警表「级别」列 severity → 中文标签
    await activateTab(wrapper, "备份");
    expect(wrapper.html()).toContain("2 KB"); // 「大小」列 fmtBytes
    expect(wrapper.html()).toContain("下载"); // 「操作」列下载按钮
  }, 20000);

  it("单个端点失败只隐藏对应 Tab，不整页白屏", async () => {
    listTaskRuns.mockRejectedValueOnce(new Error("boom"));
    const wrapper = await mountWithAntd();
    await new Promise((r) => setTimeout(r, 0));
    // 整页未白屏：标题仍在、状态卡片（status 端点成功）照常渲染
    expect(wrapper.text()).toContain("运维");
    expect(wrapper.findAll(".ant-card").length).toBeGreaterThanOrEqual(1);
    // 失败的「任务」Tab 隐藏，其余「告警」Tab 保留
    const tabTexts = wrapper.findAll(".ant-tabs-tab").map((t) => t.text());
    expect(tabTexts).not.toContain("任务");
    expect(tabTexts).toContain("告警");
    expect(fetchOpsStatus).toHaveBeenCalled();
  }, 20000);
});
