// Alert copy + identity rules (platform design v0.2, BAT-28 CANONICAL UPDATE).
// BINDING for every ops surface — the Queue, Incident view, Home detail, and
// History all render through these helpers so the rules can't drift per screen.
//
// §4.1 Alert copy principles
//   * Primary copy is plain language a stressed family member understands.
//   * The technical state lives in a small code chip beside it (never inline).
//   * No internal jargon ("terminus", "CSI", "cascade step") in primary copy.
//   * Numbers only when they help a human decide.
//
// Identity honesty
//   * Show track labels, never names — "A / B (identity heuristic)" with the
//     identity-confidence score.
//   * Two tracks in one room: deterministic offset slots + per-track colored
//     halos (A teal, B violet); room card lists "2 present · A, B".

// Phrases that must never appear in primary (family-facing) copy. Lower-cased;
// matching is case-insensitive. Raw state codes are checked separately.
export const JARGON = [
  "terminus",
  "csi",
  "cascade",
  "cascade step",
  "state machine",
  "transition",
];

// Fall-machine state codes. These belong in the code chip, never in primary copy.
export const STATE_CODES = [
  "NORMAL",
  "SUSPECTED",
  "PROBABLE",
  "CHECK_IN_ACTIVE",
  "CONFIRMED_FALL",
];

// Per-state plain-language situation clause (no resident, no number).
const SITUATION = {
  SUSPECTED: "Possible fall — movement matched a fall pattern",
  PROBABLE: "Likely fall — movement strongly matched a fall pattern",
  CHECK_IN_ACTIVE: "Checking in — no answer yet",
  CONFIRMED_FALL: "Fall confirmed",
  NORMAL: "All clear",
};

// Per-state resident clause. Uses the track label (A / B), never a name.
const RESIDENT_CLAUSE = {
  SUSPECTED: (label) => `resident ${label} appears to be on the floor`,
  PROBABLE: (label) => `resident ${label} appears to be on the floor`,
  CHECK_IN_ACTIVE: (label) => `resident ${label} has not answered`,
  CONFIRMED_FALL: (label) => `resident ${label} appears to be on the floor and hasn't gotten up`,
  NORMAL: (label) => `resident ${label} is up and about`,
};

// States where the match confidence genuinely helps a human triage. We omit the
// number once a fall is confirmed (it no longer changes the decision) and for
// NORMAL — "numbers only when they help a human decide".
const SHOW_CONFIDENCE = new Set(["SUSPECTED", "PROBABLE"]);

function pct(confidence) {
  if (confidence == null || Number.isNaN(confidence)) return "";
  return ` (${Math.round(confidence * 100)}%)`;
}

// Primary, family-readable line for a fall situation. Example (the §4.1 sample):
//   primaryCopy({ state: "SUSPECTED", confidence: 0.91, trackLabel: "A" })
//   → "Possible fall — movement matched a fall pattern (91%) and resident A
//      appears to be on the floor"
export function primaryCopy({ state, confidence, trackLabel } = {}) {
  const situation = SITUATION[state] || "Status update";
  const number = SHOW_CONFIDENCE.has(state) ? pct(confidence) : "";
  const label = trackLabel || "A";
  const clause = RESIDENT_CLAUSE[state];
  if (!clause) return `${situation}${number}`;
  return `${situation}${number} and ${clause(label)}`;
}

// The small technical chip shown beside the primary copy, e.g. "SUSPECTED · 0.91".
// Confidence is appended only when present.
export function codeChip({ state, confidence } = {}) {
  const code = state || "—";
  if (confidence == null || Number.isNaN(confidence)) return code;
  return `${code} · ${confidence.toFixed(2)}`;
}

// True if any banned jargon (or a raw state code) appears in a primary string.
// Used by the surfaces (and tests) to guard family-facing copy.
export function hasJargon(text) {
  if (!text) return false;
  const lower = text.toLowerCase();
  if (JARGON.some((word) => lower.includes(word))) return true;
  return STATE_CODES.some((code) => text.includes(code));
}

// -- Identity honesty -------------------------------------------------------

// Per-track halo colors. A = teal, B = violet (design v0.2); anonymous guest
// tracks and any further labels fall back to a neutral slate.
export const TRACK_COLORS = {
  A: "#14b8a6", // teal
  B: "#8b5cf6", // violet
};
export const GUEST_COLOR = "#64748b"; // slate

export function trackColor(label) {
  return TRACK_COLORS[label] || GUEST_COLOR;
}

// The honest label for a track: its track id, never a resident name.
export function identityLabel(track) {
  return track?.label ?? track?.track_id ?? "?";
}

// Identity caption with the heuristic disclaimer + confidence, e.g.
// "A (identity heuristic · 0.80)". Guest tracks carry no identity claim.
export function identityCaption(track) {
  const label = identityLabel(track);
  const conf = track?.identity_confidence;
  if (conf == null || conf === 0) return `${label} (identity heuristic)`;
  return `${label} (identity heuristic · ${conf.toFixed(2)})`;
}

// Room-card occupancy summary, e.g. "2 present · A, B" or "empty".
export function roomPresenceCaption(tracks) {
  const list = tracks || [];
  if (list.length === 0) return "empty";
  const labels = list.map((t) => identityLabel(t)).join(", ");
  return `${list.length} present · ${labels}`;
}

// Deterministic offset slot for the Nth track sharing a room, so two people in
// one room render in stable, non-overlapping positions (no jitter between
// frames). Returns a {dx, dy} pixel offset from the room center.
const SLOT_OFFSETS = [
  { dx: 0, dy: 0 },
  { dx: -22, dy: -14 },
  { dx: 22, dy: 14 },
  { dx: -22, dy: 14 },
  { dx: 22, dy: -14 },
];
export function trackSlot(indexInRoom) {
  return SLOT_OFFSETS[indexInRoom % SLOT_OFFSETS.length];
}
