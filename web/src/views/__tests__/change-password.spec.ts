import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChangePasswordView from "../ChangePasswordView.vue";
import { useAuthStore } from "../../stores/auth";

vi.mock("../../api/auth", () => ({
  changeOwnPassword: vi.fn().mockResolvedValue(undefined),
}));

let pinia: ReturnType<typeof createPinia>;

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
  return mount(ChangePasswordView, { global: { plugins: [Antd, pinia] } });
}

/** 按钮文本规范化：Antd 会给两字中文插空格（"取消" 渲染成 "取 消"） */
function buttonTexts(wrapper: any): string[] {
  return wrapper.findAll("button").map((b: any) => b.text().replace(/\s/g, ""));
}

function setUser(mustChange: boolean) {
  useAuthStore().user = {
    id: 1, username: "u", role: "employee", created_at: "",
    status: "active", must_change_password: mustChange,
  };
}

function fillInputs(wrapper: ReturnType<typeof mount> extends never ? never : any, values: string[]) {
  const inputs = wrapper.findAll('input[type="password"]');
  values.forEach((v, i) => {
    (inputs[i].element as HTMLInputElement).value = v;
    inputs[i].element.dispatchEvent(new Event("input"));
  });
}

describe("ChangePasswordView", () => {
  beforeEach(() => {
    localStorage.clear();
    // pinia 必须在 useAuthStore() 之前激活（setUser 会用它）
    pinia = createPinia();
    setActivePinia(pinia);
  });

  it("自愿改密：不显示强制提示，有取消按钮", async () => {
    setUser(false);
    const wrapper = await mountWithAntd();
    await flushPromises();

    expect(wrapper.text()).toContain("修改密码");
    expect(wrapper.text()).not.toContain("管理员已重置你的密码");
    expect(buttonTexts(wrapper)).toContain("取消");
  }, 20000);

  it("强制改密：显示提示且隐藏取消（避免绕过）", async () => {
    setUser(true);
    const wrapper = await mountWithAntd();
    await flushPromises();

    expect(wrapper.text()).toContain("管理员已重置你的密码");
    expect(buttonTexts(wrapper)).not.toContain("取消");
  }, 20000);

  it("新密码不足 8 位 → 不发请求", async () => {
    setUser(false);
    const wrapper = await mountWithAntd();
    await flushPromises();

    fillInputs(wrapper, ["oldpass123", "short", "short"]);
    await flushPromises();
    const submit = wrapper.findAll("button").find((b: any) => b.text().replace(/\s/g, "").includes("确认修改"));
    await submit!.trigger("click");
    await flushPromises();

    const api = await import("../../api/auth");
    expect(api.changeOwnPassword).not.toHaveBeenCalled();
  }, 20000);

  it("两次输入不一致 → 不发请求", async () => {
    setUser(false);
    const wrapper = await mountWithAntd();
    await flushPromises();

    fillInputs(wrapper, ["oldpass123", "newpass1234", "newpass9999"]);
    await flushPromises();
    const submit = wrapper.findAll("button").find((b: any) => b.text().replace(/\s/g, "").includes("确认修改"));
    await submit!.trigger("click");
    await flushPromises();

    const api = await import("../../api/auth");
    expect(api.changeOwnPassword).not.toHaveBeenCalled();
  }, 20000);
});
