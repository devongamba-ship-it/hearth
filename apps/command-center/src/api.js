// Data layer (BAT-29): the Command Center reads the home layout once and then
// subscribes to the 1 Hz live feed, both from the ingest service.
//
// In dev, vite.config.js proxies /ingest → :8000 for HTTP and WS alike, so the
// browser stays same-origin. Override the base with VITE_INGEST_BASE. Set
// VITE_USE_MOCK=1 to drive the scene from src/mockFrames.js with no backend at
// all (the default when no ingest service is reachable — sales demos).

import { mockLayout, mockFrameStream } from "./mockFrames.js";

const INGEST = import.meta.env.VITE_INGEST_BASE || "/ingest";
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";
export const DEFAULT_HOME = import.meta.env.VITE_HOME_ID || "rosewood";

/** Fetch the home layout (rooms / nodes / links / resident track). */
export async function getLayout(homeId = DEFAULT_HOME) {
  if (USE_MOCK) return mockLayout;
  const url = `${INGEST}/homes/${encodeURIComponent(homeId)}/layout`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  return res.json();
}

// Resolve the ws:// (or wss://) URL for the live feed from the HTTP base. A
// relative base (the dev proxy, "/ingest") is resolved against the page origin.
function liveUrl(homeId) {
  const path = `/homes/${encodeURIComponent(homeId)}/live`;
  const base = INGEST.startsWith("http")
    ? INGEST
    : `${window.location.origin}${INGEST}`;
  const u = new URL(base + path);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}

/**
 * Open the live feed. Calls `onMessage(parsedMsg)` for each WS frame. Returns a
 * handle with `.close()`. In mock mode a timer replays mockFrameStream() at the
 * given cadence instead of opening a socket.
 *
 * `opts.onStatus(connected)` (optional) reports the WS connection state — true
 * on open, false on close/error — so the data-source badge can show "feed
 * offline" (BAT-34). Mock mode reports connected immediately.
 *
 * `deps` lets tests inject a fake WebSocket / timer; production uses globals.
 */
export function openLiveFeed(homeId, onMessage, opts = {}) {
  const { intervalMs = 1000, onStatus } = opts;
  if (USE_MOCK || opts.mock) {
    onStatus?.(true);
    return replayMock(onMessage, intervalMs, opts);
  }
  const Socket = opts.WebSocket || window.WebSocket;
  const ws = new Socket(liveUrl(homeId));
  ws.onopen = () => onStatus?.(true);
  ws.onclose = () => onStatus?.(false);
  ws.onerror = () => onStatus?.(false);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      /* ignore malformed frame */
    }
  };
  return {
    close: () => ws.close(),
    socket: ws,
  };
}

function replayMock(onMessage, intervalMs, opts) {
  const frames = opts.frames || mockFrameStream();
  const setTimer = opts.setInterval || setInterval;
  const clearTimer = opts.clearInterval || clearInterval;
  let i = 0;
  const id = setTimer(() => {
    const batch = frames[i % frames.length];
    i += 1;
    for (const msg of batch) onMessage(msg);
  }, intervalMs);
  return { close: () => clearTimer(id) };
}
