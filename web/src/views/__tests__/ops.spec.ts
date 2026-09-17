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
});
