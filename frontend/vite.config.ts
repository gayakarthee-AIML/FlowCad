import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Vite server proxies API calls to the FastAPI backend,
// so the browser only ever talks to one origin and no key reaches it.
const backend = process.env.FLOWCAD_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: { output: { manualChunks: { three: ["three"] } } },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/health": backend,
    },
  },
});
