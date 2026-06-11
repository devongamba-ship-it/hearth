// Small formatting helpers shared by the screens.

// Epoch seconds → a short local clock time, e.g. "8:12 AM". Tolerant of missing
// values (the cascade/state-machine rows always carry timestamps, but defensive
// here keeps the replay from throwing on a partial row).
export function clockTime(epochSeconds) {
  if (!epochSeconds && epochSeconds !== 0) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// Seconds between two epoch values as a "+1m 0s" offset, for the replay tracks.
export function offset(fromEpoch, toEpoch) {
  if (fromEpoch == null || toEpoch == null) return "";
  const secs = Math.max(0, Math.round(toEpoch - fromEpoch));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `+${m}m ${s}s` : `+${s}s`;
}

// Human label for a fall-machine state.
const STATE_LABEL = {
  NORMAL: "Normal",
  SUSPECTED: "Possible fall",
  PROBABLE: "Likely fall",
  CHECK_IN_ACTIVE: "Checking in",
  CONFIRMED_FALL: "Confirmed fall",
};
export function stateLabel(state) {
  return STATE_LABEL[state] || state;
}

// Human label for a cascade event kind.
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
