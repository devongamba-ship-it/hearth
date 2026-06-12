// Data layer (BAT-25): the caregiver app polls the ingest + escalation services.
//
// In dev, vite.config.js proxies /ingest → :8000 and /escalation → :8001 so the
// browser makes same-origin requests (no CORS). Override the bases with
// VITE_INGEST_BASE / VITE_ESCALATION_BASE for other deployments. Set
// VITE_USE_MOCK=1 to render from mockData.js with no backend at all.

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

// -- ingest service ---------------------------------------------------------

export function getStatus() {
  if (USE_MOCK) return Promise.resolve(mock.mockStatus);
  return getJSON(`${INGEST}/api/status`);
}

export function getLayout(homeId = "home") {
  if (USE_MOCK) return Promise.resolve(mock.mockLayout);
  return getJSON(`${INGEST}/homes/${encodeURIComponent(homeId)}/layout`);
}

export function getTrends(homeId = "home", window = "7d") {
  if (USE_MOCK) return Promise.resolve(mock.mockTrends);
  return getJSON(`${INGEST}/homes/${encodeURIComponent(homeId)}/trends?window=${window}`);
}

export function getActivity(date) {
  if (USE_MOCK) return Promise.resolve(mock.mockActivity);
  const q = date ? `?date=${encodeURIComponent(date)}` : "";
  return getJSON(`${INGEST}/api/activity${q}`);
}

export function getFallIncidents() {
  if (USE_MOCK) return Promise.resolve({ incidents: mock.mockFallIncidents });
  return getJSON(`${INGEST}/api/fall-incidents`);
}

export function getFallIncidentDetail(incidentId) {
  if (USE_MOCK) return Promise.resolve(mock.mockFallIncidentDetail);
  return getJSON(`${INGEST}/api/fall-incidents/${encodeURIComponent(incidentId)}`);
}

// -- escalation service -----------------------------------------------------

export function getActiveIncidents() {
  if (USE_MOCK) return Promise.resolve({ incidents: mock.mockActiveIncidents });
  return getJSON(`${ESCALATION}/api/incidents/active`);
}

export function getIncidents() {
  if (USE_MOCK) return Promise.resolve({ incidents: mock.mockIncidents });
  return getJSON(`${ESCALATION}/api/incidents`);
}

export function getIncidentDetail(incidentId) {
  if (USE_MOCK) {
    return Promise.resolve({ ...mock.mockIncidents[0], cascade: mock.mockCascade });
  }
  return getJSON(`${ESCALATION}/api/incidents/${encodeURIComponent(incidentId)}`);
}

export function claimIncident(incidentId, by) {
  if (USE_MOCK) {
    return Promise.resolve({
      id: incidentId, status: "claimed", claimed_by: by, claimed_now: true,
    });
  }
  return postJSON(`${ESCALATION}/incidents/${encodeURIComponent(incidentId)}/claim`, { by });
}

// Resident "I'm OK / pause" quiet window (BAT-39). Suppresses non-fall wellness
// alerts for the window; a real fall always pages. GET drives the countdown.
export function getQuietStatus() {
  if (USE_MOCK) return Promise.resolve(mock.mockQuietStatus);
  return getJSON(`${ESCALATION}/api/quiet`);
}

export function startQuiet({ hours, minutes, actor = "resident", source = "app" } = {}) {
  if (USE_MOCK) {
    const secs = (hours || 0) * 3600 + (minutes || 0) * 60;
    return Promise.resolve({ created: true, remaining_seconds: secs, actor, source });
  }
  return postJSON(`${ESCALATION}/api/quiet`, { hours, minutes, actor, source });
}

export function cancelQuiet(by = "You") {
  if (USE_MOCK) return Promise.resolve({ canceled: true });
  return postJSON(`${ESCALATION}/api/quiet/cancel`, { by });
}

// Care circle has no persistence endpoint yet (BAT-31 owns the registry); the
// app manages reorder locally and seeds from mock data.
export function getCareCircle() {
  return Promise.resolve(mock.mockCareCircle);
}
