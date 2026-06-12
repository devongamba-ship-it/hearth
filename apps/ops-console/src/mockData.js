// Offline demo data for the ops console (BAT-28).
//
// With VITE_USE_MOCK=1 the console renders with no backend — useful for a demo
// or a Lighthouse/axe run. Shapes mirror the real ingest + escalation responses
// exactly, so components don't know the difference. Live mode (the default)
// talks to the two services; see api.js.
//
// Some surfaces (the 14-day History chart, threshold config) have no real
// backend source yet — the ingest trends/activity endpoints are themselves
// flagged `stub`. Those rows are marked `stub: true` here and labelled in the UI
// so an operator never mistakes representative data for live readings.

const T0 = 1718100000; // a fixed reference epoch for deterministic demo data

// -- escalation incidents (the queue + history) -----------------------------

export const mockIncidents = [
  {
    id: "esc-active-1",
    home_name: "Rosewood",
    room: "Bathroom",
    status: "active",
    created_at: T0 + 600,
    source_incident_id: "fall-active-1",
    claimed_by: null,
    claimed_at: null,
    claim_deadline: null,
    disposition: null,
    disposition_reason: null,
    resolved_at: null,
    next_action: "voice_calls",
    next_action_at: T0 + 660,
  },
  {
    id: "esc-claimed-1",
    home_name: "Maple Court",
    room: "Living room",
    status: "claimed",
    created_at: T0 + 300,
    source_incident_id: "fall-claimed-1",
    claimed_by: "Dana",
    claimed_at: T0 + 360,
    claim_deadline: T0 + 360 + 15 * 60,
    disposition: null,
    disposition_reason: null,
    resolved_at: null,
    next_action: null,
    next_action_at: null,
  },
  {
    id: "esc-resolved-1",
    home_name: "Rosewood",
    room: "Kitchen",
    status: "resolved",
    created_at: T0 - 3600,
    source_incident_id: "fall-resolved-1",
    claimed_by: "Dana",
    claimed_at: T0 - 3540,
    claim_deadline: null,
    disposition: "recovered",
    disposition_reason: "tripped, got up fine",
    resolved_at: T0 - 3400,
    next_action: null,
    next_action_at: null,
  },
];

export const mockActiveIncidents = mockIncidents.filter((i) => i.status !== "resolved");

export const mockCascade = [
  { at: T0 + 600, kind: "incident_created", detail: "fall in Bathroom" },
  { at: T0 + 600, kind: "alert_broadcast", detail: "alerted 2 contacts" },
  { at: T0 + 660, kind: "voice_calls", detail: "voice ladder in priority order" },
];

export function mockIncidentDetail(id) {
  const incident = mockIncidents.find((i) => i.id === id) || mockIncidents[0];
  return { ...incident, cascade: mockCascade };
}

// -- ingest: fall state-machine replay --------------------------------------

export const mockFallIncidentDetail = {
  incident_id: "fall-active-1",
  node_id: 3,
  room: "Bathroom",
  transitions: [
    { at: T0 + 590, reading_ts: 30000, node_id: 3, from_state: "NORMAL", to_state: "SUSPECTED", kind: "transition", reason: null },
    { at: T0 + 595, reading_ts: 35000, node_id: 3, from_state: "SUSPECTED", to_state: "PROBABLE", kind: "transition", reason: null },
    { at: T0 + 596, reading_ts: 36000, node_id: 3, from_state: "PROBABLE", to_state: "CHECK_IN_ACTIVE", kind: "transition", reason: null },
    { at: T0 + 600, reading_ts: 40000, node_id: 3, from_state: "CHECK_IN_ACTIVE", to_state: "CONFIRMED_FALL", kind: "confirmed_fall", reason: null },
  ],
};

// -- ingest: layout + nodes (sensing health) --------------------------------

export const mockLayout = {
  home_id: "Rosewood",
  rooms: [
    { node_id: 1, room: "Kitchen", bedroom: false },
    { node_id: 2, room: "Bedroom", bedroom: true },
    { node_id: 3, room: "Bathroom", bedroom: false },
    { node_id: 4, room: "Living room", bedroom: false },
  ],
  geometry_stub: true,
};

export const mockNodes = {
  nodes: [
    { node_id: 1, ts: 130000, presence: true, motion: 0.4, confidence: 0.9, fall_flag: false, occupancy: 1 },
    { node_id: 2, ts: 129000, presence: false, motion: 0.0, confidence: 0.8, fall_flag: false, occupancy: 0 },
    { node_id: 3, ts: 40000, presence: true, motion: 0.9, confidence: 0.91, fall_flag: true, occupancy: 1 },
    { node_id: 4, ts: 128000, presence: false, motion: 0.0, confidence: 0.7, fall_flag: false, occupancy: 0 },
  ],
};

export const mockTrends = {
  home_id: "Rosewood",
  window: "7d",
  stub: true,
  cards: [
    { key: "sleep_window", label: "Sleep window", value: "11:10pm – 6:50am", delta: "about the same as usual" },
    { key: "active_minutes", label: "Active minutes", value: "150 / day", delta: "a little more than usual" },
    { key: "bathroom_count", label: "Bathroom visits", value: "6 / day", delta: "about the same as usual" },
  ],
};

// Threshold config has no backend read/write endpoint yet (the detection config
// lives server-side in detection.yaml). The console shows the current values
// read-only, flagged stub, with edits disabled — a write API is a follow-up.
export const mockThresholds = {
  stub: true,
  editable: false,
  values: [
    { key: "impact_motion", label: "Impact motion threshold", value: "0.80" },
    { key: "stillness_secs", label: "Stillness window", value: "30 s" },
    { key: "checkin_timeout_secs", label: "Check-in timeout", value: "45 s" },
    { key: "quiet_hours", label: "Quiet hours", value: "10:00pm – 7:00am" },
  ],
};

// -- live mesh frame (one WS room_activity message) -------------------------

export const mockLiveFrame = {
  type: "room_activity",
  home_id: "Rosewood",
  source: "simulated", // backendless demo data → the source badge reads "simulated" (BAT-34)
  ts: T0 + 601,
  tracking_degraded: false,
  rooms: [
    { room: "Kitchen", activity_index: 12, occupancy_count: 0 },
    { room: "Bedroom", activity_index: 0, occupancy_count: 0 },
    { room: "Bathroom", activity_index: 78, occupancy_count: 1 },
    { room: "Living room", activity_index: 20, occupancy_count: 1 },
  ],
  tracks: [
    { track_id: "A", label: "A", room: "Bathroom", confidence: 0.91, identity_confidence: 0.8, state: "fall_watch" },
    { track_id: "guest-1", label: "guest-1", room: "Living room", confidence: 0.6, identity_confidence: 0.0, state: "guest" },
  ],
};

export const mockLiveEvents = [
  { type: "event", event: "room_transition", track_id: "A", from_room: "Living room", room: "Bathroom", ts: T0 + 598 },
  { type: "event", event: "sensing_dropout", node_id: 2, room: "Bedroom", silent_secs: 14.0, ts: T0 + 599 },
];

// -- care circle (contact panel) --------------------------------------------
// No persistence endpoint yet (BAT-31 owns the registry); seeded from mock.

export const mockCareCircle = [
  { id: "c1", name: "Dana", relation: "Daughter", channels: ["push", "sms", "call"], priority: 1 },
  { id: "c2", name: "Marcus", relation: "Son", channels: ["sms"], priority: 2 },
  { id: "c3", name: "Pat (neighbour)", relation: "Neighbour", channels: ["call"], priority: 3 },
];

// -- history: 14-day active-minutes chart -----------------------------------
// No per-day history endpoint exists (the ingest trends endpoint is a stub and
// the readings table has no wall clock). Representative series, flagged stub.

export const mockDailyActiveMinutes = {
  stub: true,
  days: Array.from({ length: 14 }, (_, i) => ({
    // 14 days ending "today"; minutes wobble around a 150-minute baseline.
    epoch: T0 - (13 - i) * 86400,
    active_minutes: [140, 155, 132, 161, 149, 158, 120, 165, 151, 144, 159, 138, 150, 147][i],
  })),
};
