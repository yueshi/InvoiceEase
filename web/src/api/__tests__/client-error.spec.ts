// 统一错误提示：必须能读懂 FastAPI 的**两种** detail 形态
// 历史 bug：detail 为数组（pydantic 校验错误）时直接丢给 message.error → 显示 "[object Object]"
import { describe, expect, it, vi } from "vitest";
import { message } from "ant-design-vue";
import { errorMessage } from "../client";

function captureMessage(): string[] {
  const seen: string[] = [];
  vi.spyOn(message, "error").mockImplementation((content: unknown) => {
    seen.push(String(content));
    return {} as never;
  });
  return seen;
}

describe("errorMessage", () => {
  it("detail 是字符串（业务错误）→ 原样显示", () => {
    const seen = captureMessage();
    errorMessage({ response: { data: { detail: "密码至少 8 位" } } });
    expect(seen).toEqual(["密码至少 8 位"]);
  });

  it("**detail 是数组（FastAPI 校验错误）→ 拼成可读文本，不得出现 [object Object]**", () => {
    const seen = captureMessage();
    errorMessage({
      response: {
        data: {
          detail: [
            {
              type: "string_too_short",
              loc: ["body", "new_password"],
              msg: "String should have at least 8 characters",
              input: "",
            },
          ],
        },
      },
    });
    expect(seen).toHaveLength(1);
    expect(seen[0]).not.toContain("[object Object]");
    expect(seen[0]).toContain("new_password"); // 指出是哪个字段
    expect(seen[0]).toContain("8 characters"); // 带上原因
  });

  it("数组含多条 → 合并", () => {
    const seen = captureMessage();
    errorMessage({
      response: {
        data: { detail: [{ loc: ["body", "a"], msg: "A 错" }, { loc: ["body", "b"], msg: "B 错" }] },
      },
    });
    expect(seen[0]).toContain("A 错");
    expect(seen[0]).toContain("B 错");
  });

  it("无响应（网络错误）/ 空 detail → 用兜底文案", () => {
    let seen = captureMessage();
    errorMessage(new Error("Network Error"), "重置失败");
    expect(seen).toEqual(["重置失败"]);

    seen = captureMessage();
    errorMessage({ response: { data: {} } }, "重置失败");
    expect(seen).toEqual(["重置失败"]);

    seen = captureMessage();
    errorMessage({ response: { data: { detail: [] } } }, "重置失败");
    expect(seen).toEqual(["重置失败"]);
  });
});
