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
    if (error.response?.status === 401 && !error.config?.url?.includes("/auth/login")) {
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
  const resp = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(resp.data);
  const a = document.createElement("a");
  a.href = url;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * 统一错误提示：**自身弹出 error toast**，调用处直接 `errorMessage(e)` 即可。
 * 不要再包一层 `message.error(errorMessage(e))`——本函数返回 void，
 * 包一层会弹出空消息（历史 bug）。
 */
export function errorMessage(e: unknown, fallback = "请求失败"): void {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  message.error(detail || fallback);
}

export async function fetchBlobUrl(path: string): Promise<string> {
  // 带鉴权头的 blob 拉取（iframe/img 无法附加 Authorization header，故用 axios + objectURL）
  const resp = await api.get(path, { responseType: "blob", timeout: 120000 });
  return URL.createObjectURL(resp.data);
}
