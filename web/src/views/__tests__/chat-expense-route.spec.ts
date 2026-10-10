// 路由注册契约（单独文件：本文件不 mock vue-router，才能 import 真正的路由表）
import { describe, expect, it } from "vitest";
import { routes } from "../../router/index";

describe("对话式报销路由", () => {
  it("/expenses/chat 已注册且标题为「报销助手」", () => {
    const r = routes.find((x) => x.path === "/expenses/chat");
    expect(r).toBeTruthy();
    expect(r?.meta?.title).toBe("报销助手");
  });

  it("报销入口顺序：列表页在前、对话页紧随（深链与导航一致性）", () => {
    const paths = routes.map((r) => r.path);
    expect(paths.indexOf("/expenses/chat")).toBe(paths.indexOf("/expenses") + 1);
  });
});
