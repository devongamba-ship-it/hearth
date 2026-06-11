/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Command Center talks to the ingest service for the home layout
// (GET /homes/{id}/layout) and the 1 Hz live feed (WS /homes/{id}/live). In dev
// we proxy /ingest → :8000 so the browser makes same-origin requests (HTTP and
// WS both) and never hits CORS. Override the upstream with HEARTH_INGEST_URL.
// Set VITE_USE_MOCK=1 to drive the scene from src/mockFrames.js with no backend.
const INGEST = process.env.HEARTH_INGEST_URL || "http://127.0.0.1:8000";

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
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    css: true,
  },
});
