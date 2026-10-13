import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 127.0.0.1 rather than localhost: Node may resolve localhost to ::1 while uvicorn binds IPv4.
const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1000 },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, ws: true, changeOrigin: true },
    },
  },
});
