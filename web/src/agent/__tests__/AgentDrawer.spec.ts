// web/src/agent/__tests__/AgentDrawer.spec.ts
// 挤压式侧栏：宽度渲染 / 拖拽调宽（含光标锁定）/ 双击复位 / 关闭不渲染
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgentDrawer from "../components/AgentDrawer.vue";
import { useAgentStore } from "../store";

// 组件用 useRoute 拼上下文 chip
vi.mock("vue-router", () => ({ useRoute: () => ({ path: "/invoices", query: {} }) }));

describe("AgentDrawer（挤压式侧栏）", () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    localStorage.clear(); // 宽度偏好落 localStorage，用例间隔离
    pinia = createPinia();
    setActivePinia(pinia);
  });

  function mountOpen() {
    const store = useAgentStore();
    store.drawerOpen = true;
    return { store, wrapper: mount(AgentDrawer, { global: { plugins: [pinia] } }) };
  }

  it("打开时渲染 aside.agent-panel，宽度取 store.drawerWidth（默认 440px）", () => {
    const { wrapper } = mountOpen();
    const panel = wrapper.find("aside.agent-panel");
    expect(panel.exists()).toBe(true);
    expect((panel.element as HTMLElement).style.width).toBe("440px");
  });

  it("拖拽手柄：往左拖加宽 + 全局光标锁定，松手后不再响应", async () => {
    window.innerWidth = 1280; // 上界 = min(720, 1280*0.6) = 720，640 可达
    const { store, wrapper } = mountOpen();

    await wrapper.find(".resize-handle").trigger("mousedown", { clientX: 500 });
    expect(document.body.style.cursor).toBe("col-resize");
    window.dispatchEvent(new MouseEvent("mousemove", { clientX: 300 }));
    expect(store.drawerWidth).toBe(640); // 440 + (500 - 300)：往左拖 = 加宽

    window.dispatchEvent(new MouseEvent("mouseup"));
    expect(document.body.style.cursor).toBe(""); // 锁定已还原
    window.dispatchEvent(new MouseEvent("mousemove", { clientX: 100 }));
    expect(store.drawerWidth).toBe(640); // 已松手，监听已摘除

    window.innerWidth = 1024;
  });

  it("双击手柄复位 440", async () => {
    const { store, wrapper } = mountOpen();
    store.setDrawerWidth(600);
    await wrapper.find(".resize-handle").trigger("dblclick");
    expect(store.drawerWidth).toBe(440);
  });

  it("面板关闭时不渲染 aside", () => {
    const store = useAgentStore();
    store.drawerOpen = false;
    const wrapper = mount(AgentDrawer, { global: { plugins: [pinia] } });
    expect(wrapper.find("aside.agent-panel").exists()).toBe(false);
  });
});
