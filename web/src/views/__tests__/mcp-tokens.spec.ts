import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import McpTokensView from "../McpTokensView.vue";
import { useAuthStore } from "../../stores/auth";

vi.mock("../../api/mcpTokens", () => ({
  listMcpTokens: vi.fn().mockResolvedValue([
    {
      id: 1, name: "张三的 WorkBuddy", token_prefix: "abc12345", user_id: 2,
      scopes: ["invoice:read", "expense:write"], expires_at: "2026-12-14T00:00:00",
      revoked_at: null, last_used_at: null, created_at: "2026-09-15T00:00:00",
      state: "active", owner_username: null,
    },
  ]),
  issueMcpToken: vi.fn().mockResolvedValue({
    token: {
      id: 2, name: "新令牌", token_prefix: "xyz98765", user_id: 2,
      scopes: ["invoice:read"], expires_at: null, revoked_at: null,
      last_used_at: null, created_at: "2026-09-15T00:00:00",
      state: "active", owner_username: null,
    },
    plaintext: "PLAINTEXT-ONLY-ONCE-abcdefghijklmnopqrstuvwxyz",
  }),
  revokeMcpToken: vi.fn().mockResolvedValue({}),
  fetchMcpScopes: vi.fn().mockResolvedValue({
    scopes: ["invoice:read", "invoice:write", "expense:read"],
    role_defaults: { employee: ["invoice:read"], admin: ["invoice:read", "invoice:write"] },
  }),
}));

/** 断言弹窗内容需真实渲染 Antd（未注册时组件不解析、props 不进 DOM） */
async function mountWithAntd() {
  // Antd 响应式组件依赖 matchMedia（jsdom 无此 API）
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
  return mount(McpTokensView, { global: { plugins: [Antd] } });
}

function clickInBody(selector: string, text: string) {
  const el = [...document.body.querySelectorAll(selector)].find((e) =>
    e.textContent?.includes(text),
  ) as HTMLElement | undefined;
  if (!el) throw new Error(`未找到「${text}」(${selector})`);
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

describe("McpTokensView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    useAuthStore().user = {
      id: 1, username: "admin", role: "admin", created_at: "2026-09-15T00:00:00",
    };
  });

  it("挂载时加载令牌列表并渲染状态", async () => {
    const wrapper = await mountWithAntd();
    await flushPromises();

    const api = await import("../../api/mcpTokens");
    expect(api.listMcpTokens).toHaveBeenCalled();
    expect(wrapper.text()).toContain("张三的 WorkBuddy");
    expect(wrapper.text()).toContain("有效");
  }, 20000);

  it("签发后弹出明文，且只在这一次出现", async () => {
    const wrapper = await mountWithAntd();
    await flushPromises();

    // 页面自身的按钮在 wrapper 里（mount 不挂到 document.body），
    // 而弹窗是 teleport 到 body 的——两类元素查询方式不同。
    const openBtn = wrapper.findAll("button").find((b) => b.text().includes("签发新令牌"));
    await openBtn!.trigger("click");
    await flushPromises();

    // 填名称（弹窗 teleport 到 body）
    const nameInput = [...document.body.querySelectorAll("input")].find((i) =>
      (i as HTMLInputElement).placeholder?.includes("WorkBuddy"),
    ) as HTMLInputElement;
    expect(nameInput).toBeTruthy();
    nameInput.value = "新令牌";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    // 测试环境 Antd 是英文 locale（按钮为 OK/Cancel），按 primary class 选更稳
    const okBtn = document.body.querySelector(
      ".ant-modal-footer button.ant-btn-primary",
    ) as HTMLElement;
    expect(okBtn).toBeTruthy();
    okBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();

    const api = await import("../../api/mcpTokens");
    expect(api.issueMcpToken).toHaveBeenCalled();
    // 明文在 readonly textarea 里——**表单控件的值不进 textContent**，
    // 必须读 value 属性（否则断言永远失败，且失败原因会指向错误的方向）
    const shownPlaintext = () =>
      [...document.body.querySelectorAll("textarea")].map((t) => t.value).join("");
    expect(shownPlaintext()).toContain("PLAINTEXT-ONLY-ONCE");
    expect(document.body.textContent).toContain("明文只显示这一次"); // 警示文案

    // 关闭后页面上不再有明文（一次性展示）
    clickInBody("button", "我已复制");
    await flushPromises();
    expect(shownPlaintext()).not.toContain("PLAINTEXT-ONLY-ONCE");
    expect(wrapper.text()).not.toContain("PLAINTEXT-ONLY-ONCE");
  }, 20000);
});
