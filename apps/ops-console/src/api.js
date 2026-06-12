// Data layer (BAT-28): the ops console reads the ingest + escalation services.
//
// In dev, vite.config.js proxies /ingest → :8000 and /escalation → :8001 (and
// upgrades the ingest WebSocket), so the browser makes same-origin requests
// (no CORS). Override the bases with VITE_INGEST_BASE / VITE_ESCALATION_BASE.
// Set VITE_USE_MOCK=1 to render from mockData.js with no backend at all.

import * as mock from "./mockData.js";

const INGEST = import.meta.env.VITE_INGEST_BASE || "/ingest";
const ESCALATION = import.meta.env.VITE_ESCALATION_BASE || "/escalation";
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  return res.json();
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const err = new Error(`${url} → HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// -- escalation service (the alert queue + incident lifecycle) --------------

export function getIncidents() {
  if (USE_MOCK) return Promise.resolve({ incidents: mock.mockIncidents });
  return getJSON(`${ESCALATION}/api/incidents`);
}

export function getActiveIncidents() {
  if (USE_MOCK) return Promise.resolve({ incidents: mock.mockActiveIncidents });
  return getJSON(`${ESCALATION}/api/incidents/active`);
}

export function getIncidentDetail(incidentId) {
  if (USE_MOCK) return Promise.resolve(mock.mockIncidentDetail(incidentId));
  return getJSON(`${ESCALATION}/api/incidents/${encodeURIComponent(incidentId)}`);
}

export function claimIncident(incidentId, by) {
  if (USE_MOCK) {
    return Promise.resolve({ id: incidentId, status: "claimed", claimed_by: by, claimed_now: true });
  }
  return postJSON(`${ESCALATION}/incidents/${encodeURIComponent(incidentId)}/claim`, { by });
}

export function resolveIncident(incidentId, { disposition, reason_code, by }) {
  if (USE_MOCK) {
    return Promise.resolve({ incident_id: incidentId, status: "resolved", disposition });
  }
  return postJSON(`${ESCALATION}/incidents/${encodeURIComponent(incidentId)}/resolve`, {
    disposition,
    reason_code,
    by,
  });
}

// -- ingest service (mesh view, state machine, sensing health) --------------

export function getLayout(homeId = "home") {
  if (USE_MOCK) return Promise.resolve(mock.mockLayout);
  return getJSON(`${INGEST}/homes/${encodeURIComponent(homeId)}/layout`);
}

export function getNodes() {
  if (USE_MOCK) return Promise.resolve(mock.mockNodes);
  return getJSON(`${INGEST}/api/nodes`);
}

export function getTrends(homeId = "home", window = "7d") {
  if (USE_MOCK) return Promise.resolve(mock.mockTrends);
  return getJSON(`${INGEST}/homes/${encodeURIComponent(homeId)}/trends?window=${window}`);
}

export function getFallIncidentDetail(incidentId) {
  if (USE_MOCK) return Promise.resolve(mock.mockFallIncidentDetail);
  return getJSON(`${INGEST}/api/fall-incidents/${encodeURIComponent(incidentId)}`);
}

// Threshold config + care circle have no backend endpoint yet (see mockData.js);
// served from mock everywhere so the surfaces render and are clearly flagged.
export function getThresholds() {
  return Promise.resolve(mock.mockThresholds);
}
export function getCareCircle() {
  return Promise.resolve(mock.mockCareCircle);
}

// The 14-day active-minutes series has no per-day backend source yet (the
// readings table is wall-clock-less); representative data, flagged stub.
export function getDailyActiveMinutes() {
  return Promise.resolve(mock.mockDailyActiveMinutes);
}

// -- BAT-43 demo mode (only present when ingest runs with HEARTH_DEMO=1) -----

// The demo control panel drives these. In mock mode they no-op so the panel is
// still clickable with no backend.
export function getDemoStatus() {
  if (USE_MOCK) {
    return Promise.resolve({ enabled: true, playing: false, scenario: null, source: "simulated" });
  }
  return getJSON(`${INGEST}/api/demo/status`);
}

export function playDemo(scenario) {
  if (USE_MOCK) return Promise.resolve({ playing: true, scenario, source: "simulated" });
  return postJSON(`${INGEST}/api/demo/play`, { scenario });
}

export function resetDemo() {
  if (USE_MOCK) return Promise.resolve({ playing: false, reset: true, source: "simulated" });
  return postJSON(`${INGEST}/api/demo/reset`, {});
}

// -- live mesh feed ---------------------------------------------------------

// Build the WebSocket URL for the 1 Hz live mesh feed. Same-origin in dev (vite
// proxies the upgrade); ws://host for an explicit ingest base.
export function liveFeedUrl(homeId = "home") {
  const path = `/homes/${encodeURIComponent(homeId)}/live`;
  if (INGEST.startsWith("http")) {
    return INGEST.replace(/^http/, "ws") + path;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${INGEST}${path}`;
}
