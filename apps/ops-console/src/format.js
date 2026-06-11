// Formatting helpers for the ops surfaces. Clinical and dense: short clock
// times, count-up age timers, compact durations.

// Epoch seconds → short local clock time, e.g. "8:12 AM". Tolerant of nulls.
export function clockTime(epochSeconds) {
  if (epochSeconds == null) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// Epoch seconds → short local date, e.g. "Jun 4".
export function shortDate(epochSeconds) {
  if (epochSeconds == null) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// A count-up "age" of an alert, in seconds, as "1:05" (m:ss) or "12s" under a
// minute. nowSeconds defaults to the wall clock so the queue ticks live.
export function ageLabel(sinceEpoch, nowSeconds = Date.now() / 1000) {
  if (sinceEpoch == null) return "—";
  const secs = Math.max(0, Math.round(nowSeconds - sinceEpoch));
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// A finished duration between two epoch values, e.g. "2m 32s" — used for an
// incident's response time (claimed_at − created_at) and total handling time.
export function duration(fromEpoch, toEpoch) {
  if (fromEpoch == null || toEpoch == null) return "—";
  const secs = Math.max(0, Math.round(toEpoch - fromEpoch));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// Human label for a cascade event kind (the incident's escalation timeline).
const CASCADE_LABEL = {
  incident_created: "Incident opened",
  alert_broadcast: "Alerted care circle (SMS / push)",
  voice_calls: "Voice calls placed",
  operator_escalation: "Operator step",
  claimed: "Claimed",
  claim_expired_resumed: "Claim expired — resumed",
  resolved: "Resolved",
};
export function cascadeLabel(kind) {
  return CASCADE_LABEL[kind] || kind;
}

// Plain-language label for a resolution disposition (the four canonical codes).
const DISPOSITION_LABEL = {
  recovered: "Recovered",
  caregiver_responding: "Caregiver responding",
  advised_call_ems: "Advised to call EMS",
  false_positive: "False positive",
};
export function dispositionLabel(disposition) {
  return DISPOSITION_LABEL[disposition] || disposition || "—";
}
