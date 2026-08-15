// 认证 store：token/user 状态 + localStorage 持久化
import { defineStore } from "pinia";
import * as authApi from "../api/auth";
import { TOKEN_KEY } from "../api/client";
import type { UserOut } from "../types";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) as string | null,
    user: null as UserOut | null,
  }),
  getters: {
    isAdmin: (s) => s.user?.role === "admin",
    role: (s) => s.user?.role ?? null,
  },
  actions: {
    async login(username: string, password: string) {
      const resp = await authApi.login(username, password);
      this.token = resp.access_token;
      this.user = resp.user;
      localStorage.setItem(TOKEN_KEY, resp.access_token);
    },
    async logout() {
      try {
        await authApi.logout();
      } finally {
        this.token = null;
        this.user = null;
        localStorage.removeItem(TOKEN_KEY);
      }
    },
    async loadMe() {
      if (!this.token) return;
      this.user = await authApi.fetchMe();
    },
  },
});
