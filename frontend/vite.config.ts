import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 生产构建：产物 frontend/dist，由后端（FastAPI）同源托管在 /
// 开发：/api 与 /media 反向代理到本机后端 8090；也可用 VITE_API_BASE 直连
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8090", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8090", changeOrigin: true },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8090", changeOrigin: true },
      "/media": { target: "http://127.0.0.1:8090", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 1400,
  },
});
