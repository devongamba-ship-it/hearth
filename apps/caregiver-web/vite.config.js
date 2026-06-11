/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the app talks to the two local services through same-origin proxies so
// the browser never hits CORS: /ingest → :8000, /escalation → :8001. Override
// the upstreams with HEARTH_INGEST_URL / HEARTH_ESCALATION_URL when they run
// elsewhere. See README for the full "npm run dev + sim" loop.
const INGEST = process.env.HEARTH_INGEST_URL || "http://127.0.0.1:8000";
const ESCALATION = process.env.HEARTH_ESCALATION_URL || "http://127.0.0.1:8001";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ingest": { target: INGEST, changeOrigin: true, rewrite: (p) => p.replace(/^\/ingest/, "") },
      "/escalation": { target: ESCALATION, changeOrigin: true, rewrite: (p) => p.replace(/^\/escalation/, "") },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    css: true,
  },
});
