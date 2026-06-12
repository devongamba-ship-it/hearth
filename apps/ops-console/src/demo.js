// Demo mode (BAT-43) — the ops console's deterministic scenario-player controls.
//
// A dev/demo-only strip of buttons that play seeded scenarios against the REAL
// ingest pipeline, so the console (and the caregiver app + command center) light
// up off real data — not a fake. The backend endpoints only exist when the
// ingest service runs with HEARTH_DEMO=1, and while it does the data-source
// badge reads "simulated" (BAT-34), so a demo can never be mistaken for live.

// The four scenarios the player offers — keys match the ingest demo endpoints.
export const DEMO_SCENARIOS = [
  {
    key: "fall_confirmed",
    label: "Fall — confirmed",
    hint: "impact then sustained stillness → escalates exactly once",
  },
  {
    key: "fall_recovered",
    label: "Fall — recovered",
    hint: "impact then gets back up → logged as a near-fall, never escalates",
  },
  {
    key: "multi_occupancy",
    label: "Multi-occupancy",
    hint: "a resident plus an anonymous guest in another room",
  },
  {
    key: "quiet_morning",
    label: "Quiet morning",
    hint: "present but near-still — a wellness signal, not an alert",
  },
];

/**
 * Whether the demo controls should show. Opt-in via `?demo` in the URL (the
 * controls are off by default so they never appear in a normal operator view).
 *
 * @param {string} search  a location search string, with or without the leading "?"
 * @returns {boolean}
 */
export function isDemoEnabled(search = "") {
  const qs = search.startsWith("?") ? search.slice(1) : search;
  return new URLSearchParams(qs).has("demo");
}
