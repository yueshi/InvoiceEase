import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import PageHeader from "../PageHeader.vue";

describe("PageHeader", () => {
  it("渲染标题；无 desc 不渲染描述节点", () => {
    const w = mount(PageHeader, { props: { title: "发票列表" } });
    expect(w.find(".page-header-title").text()).toBe("发票列表");
    expect(w.find(".page-header-desc").exists()).toBe(false);
    expect(w.find(".page-header-extra").exists()).toBe(false);
  });

  it("渲染描述与 extra 动作区", () => {
    const w = mount(PageHeader, {
      props: { title: "银行回单", desc: "筛选说明" },
      slots: { extra: '<button class="x">上传</button>' },
    });
    expect(w.find(".page-header-desc").text()).toBe("筛选说明");
    expect(w.find(".page-header-extra button.x").text()).toBe("上传");
  });
});
