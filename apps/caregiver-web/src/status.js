// Status-card composition (BAT-25).
//
// The "Mom is okay" answer comes from two services: the ingest service's coarse
// home status (all_good / sleeping / checking_in / no_activity) and the
// escalation service's active incidents (a running fall cascade, or "claimed by
// …"). An active incident always wins — a possible fall is the most important
// thing a worried daughter can see. This module is pure so the fall → claim
// transition can be unit-tested without a backend.

export const LEVELS = { GREEN: "green", AMBER: "amber", RED: "red" };

// The five canonical status-card states from the issue.
export const STATES = {
  ALL_GOOD: "all_good",
  SLEEPING: "sleeping",
  CHECKING_IN: "checking_in",
  CASCADE: "cascade",
  CLAIMED: "claimed",
  NO_ACTIVITY: "no_activity",
  UNKNOWN: "unknown",
};

const INGEST_LEVEL = {
  all_good: LEVELS.GREEN,
  sleeping: LEVELS.GREEN,
  checking_in: LEVELS.AMBER,
  no_activity: LEVELS.AMBER,
};

function newestIncident(incidents) {
  if (!incidents || incidents.length === 0) return null;
  return [...incidents].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0];
}

// Compose the status-card view-model.
//   ingestStatus    — body of GET /api/status (or null while loading)
//   activeIncidents — array from GET /api/incidents/active (or null/[])
export function composeStatus(ingestStatus, activeIncidents) {
  const incident = newestIncident(activeIncidents);

  if (incident) {
    if (incident.status === "claimed") {
      return {
        level: LEVELS.RED,
        state: STATES.CLAIMED,
        headline: `Claimed by ${incident.claimed_by || "a caregiver"}`,
        detail: `${incident.room}: a caregiver is responding.`,
        incident,
      };
    }
    return {
      level: LEVELS.RED,
      state: STATES.CASCADE,
      headline: "Possible fall — cascade running",
      detail: `${incident.room}: contacting your care circle.`,
      incident,
    };
  }

  if (!ingestStatus) {
    return {
      level: LEVELS.AMBER,
      state: STATES.UNKNOWN,
      headline: "Connecting…",
      detail: "Reaching the home device.",
      incident: null,
    };
  }

  return {
    level: INGEST_LEVEL[ingestStatus.state] || LEVELS.AMBER,
    state: ingestStatus.state,
    headline: ingestStatus.headline,
    detail: ingestStatus.detail,
    ingest: ingestStatus,
    incident: null,
  };
}

// A claim button is offered only for a live, unclaimed cascade.
export function isClaimable(view) {
  return Boolean(view.incident) && view.incident.status === "active";
}
