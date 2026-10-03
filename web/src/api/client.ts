// axios 实例：注入 token、401 拦截、文件下载与错误提示工具
import axios from "axios";
import { message } from "ant-design-vue";

export const TOKEN_KEY = "invoicing_token";

export const api = axios.create({ baseURL: "/api/v1", timeout: 15000 });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    // ticket-login 与 login 同为「会话前」端点：401 由调用方（路由守卫）处理
    // ——它要保留 redirect/expired 参数；此处硬跳会把这些参数冲掉。
    if (
      error.response?.status === 401 &&
      !error.config?.url?.includes("/auth/login") &&
      !error.config?.url?.includes("/auth/ticket-login")
    ) {
      localStorage.removeItem(TOKEN_KEY);
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  },
);

export async function downloadFile(path: string, filename: string): Promise<void> {
  // 注意：不设置 a.download —— 响应头 Content-Disposition 优先，
  // 后端已下发正确文件名（RFC 5987）；download 属性会覆盖它。
  const resp = await api.get(path, { responseType: "blob", timeout: 120000 });
  const url = URL.createObjectURL(resp.data);
  const a = document.createElement("a");
  a.href = url;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * FastAPI 的 `detail` 有**两种**形态，必须都认：
 * - 字符串：业务错误（HTTPException(422, "密码至少 8 位")）
 * - **数组**：pydantic 校验错误，元素形如 `{loc: ["body","new_password"], msg: "..."}`
 *
 * 只按字符串处理时，数组会被 `message.error` 渲染成 `[object Object]`（已复现）。
 * 顺带处理空数组——`[] || fallback` 走真值分支会弹出**空消息**（同类历史 bug）。
 */
function normalizeDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail.trim() || null;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") return item;
        const { loc, msg } = item as { loc?: unknown; msg?: unknown };
        const field = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
        const text = typeof msg === "string" ? msg : "";
        return [field, text].filter(Boolean).join(": ");
      })
      .filter(Boolean);
    return parts.length ? parts.join("；") : null;
  }
  return null;
}

/**
 * 从异常里取出可读错误文本（不弹提示）——供需自行汇总多条结果的场景用
 * （如 ExpensesView 批量加发票时逐条收集失败原因，避免双重弹窗）。
 */
export function errorText(e: unknown, fallback = "请求失败"): string {
  const err = e as { response?: { data?: { detail?: unknown } }; code?: string } | null;
  const detail = err?.response?.data?.detail;
  const normalized = normalizeDetail(detail);
  if (normalized) return normalized;
  // 网络层错误（后端未启动 / 断网 / 超时）：给可操作提示，不再笼统兜底
  // （历史体验：后端一停，任何筛选都只显示「列表加载失败」，排查方向不明）
  if (!err?.response) {
    if (err?.code === "ECONNABORTED") return "请求超时：服务器响应过慢或不可达";
    if (err?.code === "ERR_NETWORK" || err?.code === "ERR_CONNECTION_REFUSED") {
      return "无法连接服务器，请确认后端服务已启动";
    }
  }
  return fallback;
}

/**
 * 统一错误提示：**自身弹出 error toast**，调用处直接 `errorMessage(e)` 即可。
 * 不要再包一层 `message.error(errorMessage(e))`——本函数返回 void，
 * 包一层会弹出空消息（历史 bug）。
 */
export function errorMessage(e: unknown, fallback = "请求失败"): void {
  message.error(errorText(e, fallback));
}

export async function fetchBlobUrl(path: string): Promise<string> {
  // 带鉴权头的 blob 拉取（iframe/img 无法附加 Authorization header，故用 axios + objectURL）
  const resp = await api.get(path, { responseType: "blob", timeout: 120000 });
  return URL.createObjectURL(resp.data);
}
