/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The ops console talks to the same two local services as the caregiver app,
// through same-origin proxies so the browser never hits CORS: /ingest → :8000,
// /escalation → :8001. The live mesh view also opens a WebSocket to the ingest
// service (/ingest/homes/<id>/live), which vite proxies too (ws: true).
// Override the upstreams with HEARTH_INGEST_URL / HEARTH_ESCALATION_URL.
const INGEST = process.env.HEARTH_INGEST_URL || "http://127.0.0.1:8000";
const ESCALATION = process.env.HEARTH_ESCALATION_URL || "http://127.0.0.1:8001";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ingest": {
        target: INGEST,
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/ingest/, ""),
      },
      "/escalation": {
        target: ESCALATION,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/escalation/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    css: true,
  },
});
