// Offline demo data (BAT-25).
//
// With `VITE_USE_MOCK=1` the app renders without any backend — handy for a quick
// look or a Lighthouse run. The shapes mirror the real ingest/escalation
// responses exactly, so the components don't know the difference. Live mode
// (the default) talks to the two services behind the sim; see api.js.

export const mockStatus = {
  home_id: "Rosewood",
  state: "all_good",
  headline: "All good",
  detail: "Last activity: Kitchen, 2 min ago.",
  presence: true,
  fall_state: "NORMAL",
  last_activity: { room: "Kitchen", node_id: 1, seconds_ago: 120 },
  activity_ring: {
    stub: true,
    active_minutes_today: 142,
    typical_active_minutes: 160,
    pct_of_typical: 89,
    active_fraction_recent: 0.8,
  },
  as_of_uptime_ms: 130000,
  privacy: "Coarse status only. No cameras, no audio recording.",
};

export const mockActiveIncidents = [];

// No resident quiet window active by default (BAT-39).
export const mockQuietStatus = { active: false, window: null, remaining_seconds: 0 };

export const mockFallIncidents = [
  {
    incident_id: "fall-demo-1",
    node_id: 1,
    room: "Kitchen",
    started_at: 1718100000,
    confirmed_at: 1718100048,
    num_transitions: 5,
  },
];

export const mockFallIncidentDetail = {
  incident_id: "fall-demo-1",
  node_id: 1,
  room: "Kitchen",
  transitions: [
    { at: 1718100000, reading_ts: 30000, node_id: 1, from_state: "NORMAL", to_state: "SUSPECTED", kind: "transition", reason: null },
    { at: 1718100020, reading_ts: 50000, node_id: 1, from_state: "SUSPECTED", to_state: "PROBABLE", kind: "transition", reason: null },
    { at: 1718100020, reading_ts: 50000, node_id: 1, from_state: "PROBABLE", to_state: "CHECK_IN_ACTIVE", kind: "transition", reason: null },
    { at: 1718100048, reading_ts: 78000, node_id: 1, from_state: "CHECK_IN_ACTIVE", to_state: "CONFIRMED_FALL", kind: "confirmed_fall", reason: null },
  ],
};

export const mockCascade = [
  { at: 1718100048, kind: "incident_created", priority: "P1", detail: "fall in Kitchen" },
  { at: 1718100048, kind: "alert_broadcast", priority: "P1", detail: "alerted 2 contacts" },
  { at: 1718100108, kind: "voice_calls", priority: "P1", detail: "voice ladder in priority order" },
];

export const mockIncidents = [
  {
    id: "esc-demo-1",
    home_name: "Rosewood",
    room: "Kitchen",
    status: "resolved",
    created_at: 1718100048,
    source_incident_id: "fall-demo-1",
    claimed_by: "Dana",
    claimed_at: 1718100080,
    claim_deadline: null,
    disposition: "recovered",
    disposition_reason: "tripped, got up fine",
    resolved_at: 1718100200,
    next_action: null,
    next_action_at: null,
  },
];

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

export const mockActivity = {
  date: null,
  stub: true,
  blocks: [
    { room: "Bedroom", start: "06:50", end: "07:05", label: "Woke up" },
    { room: "Kitchen", start: "07:05", end: "07:40", label: "Morning routine" },
    { room: "Kitchen", start: "08:12", end: "08:40", label: "Breakfast" },
    { room: "Living room", start: "10:30", end: "11:15", label: "Resting" },
    { room: "Kitchen", start: "12:20", end: "12:55", label: "Lunch" },
  ],
};

export const mockCareCircle = [
  { id: "c1", name: "Dana", channels: ["push", "sms", "call"], priority: 1 },
  { id: "c2", name: "Marcus", channels: ["sms"], priority: 2 },
  { id: "c3", name: "Neighbour (Pat)", channels: ["call"], priority: 3 },
];
