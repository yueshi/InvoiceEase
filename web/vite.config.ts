import vue from "@vitejs/plugin-vue";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  return {
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 后端地址可用 VITE_API_TARGET 覆盖（如本机 8000 被其他进程占用时）
      "/api": { target: env.VITE_API_TARGET || "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
  };
});
