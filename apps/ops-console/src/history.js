// History model (BAT-28 CANONICAL UPDATE #5). Falls, wellness alerts, and system
// events share ONE timeline with type pills; resolved incidents append
// automatically with their response time. The UI shows 90 days; export hands
// back the full audit log. Pure helpers, unit-tested directly.

import { primaryCopy } from "./copy.js";
import { dispositionLabel, duration } from "./format.js";

export const EVENT_TYPES = ["fall", "wellness", "system"];
export const RETENTION_DAYS = 90;

// Map an escalation incident to a unified history event. A resolved incident
// carries its disposition + response time (claimed_at − created_at) as outcome.
export function incidentToEvent(incident) {
  const resolved = incident.status === "resolved";
  const outcome = resolved
    ? `${dispositionLabel(incident.disposition)} · responded ${duration(
        incident.created_at,
        incident.claimed_at,
      )}`
    : incident.status;
  const description = resolved
    ? `Fall in ${incident.room} — ${dispositionLabel(incident.disposition).toLowerCase()}`
    : primaryCopy({ state: "CONFIRMED_FALL", trackLabel: "A" });
  return {
    id: incident.id,
    ts: incident.created_at,
    type: "fall",
    description,
    room: incident.room,
    person: "A", // identity honesty: the resident track label, never a name
    outcome,
    home_name: incident.home_name,
  };
}

// Build the unified, newest-first timeline from incidents plus any wellness /
// system events, clamped to the retention window for display.
export function buildHistory(incidents = [], extraEvents = [], opts = {}) {
  const now = opts.now ?? Date.now() / 1000;
  const retentionDays = opts.retentionDays ?? RETENTION_DAYS;
  const cutoff = now - retentionDays * 86400;
  const all = [...incidents.map(incidentToEvent), ...extraEvents];
  return all
    .filter((e) => e.ts == null || e.ts >= cutoff)
    .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
}

// Serialize the FULL audit log (no retention clamp) for the export action.
export function auditLogJson(incidents = [], extraEvents = []) {
  const all = buildHistory(incidents, extraEvents, { retentionDays: Infinity });
  return JSON.stringify(all, null, 2);
}
