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
  const resp = await api.get(path, { responseType: "blob" });
  const url = URL.createObjectURL(resp.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function errorMessage(e: unknown, fallback = "请求失败"): void {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  message.error(detail || fallback);
}
