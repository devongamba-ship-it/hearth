// Backendless demo data (BAT-29) for VITE_USE_MOCK / sales demos.
//
// `mockLayout` mirrors the authored demo home ("Rosewood", apps/ingest/layout.yaml)
// in the exact shape GET /homes/{id}/layout returns. `mockFrameStream()` returns
// a looping list of per-tick message batches in the BAT-27 v0.2 WS shape,
// scripted to exercise every render path: idle, movement (room_transition),
// dwell, a second (guest) track, a fall (fall_watch + incident, red pulse), and
// a tracking_degraded tick (count badges).

export const mockLayout = {
  home_id: "rosewood",
  name: "Rosewood",
  rooms: [
    { id: "kitchen", name: "Kitchen", bedroom: false },
    { id: "living", name: "Living room", bedroom: false },
    { id: "bedroom", name: "Bedroom", bedroom: true },
    { id: "bathroom", name: "Bathroom", bedroom: false },
  ],
  nodes: [
    { id: 1, room: "kitchen", x: 0.25, y: 0.25 },
    { id: 2, room: "bedroom", x: 0.75, y: 0.25 },
    { id: 3, room: "bathroom", x: 0.75, y: 0.75 },
    { id: 4, room: "living", x: 0.25, y: 0.75 },
  ],
  links: [
    { a: 1, b: 4 },
    { a: 4, b: 3 },
    { a: 3, b: 2 },
    { a: 1, b: 2 },
  ],
  resident: { track_label: "A", identity_confidence: 0.9 },
};

// Helper: a room_activity message with the named rooms hot and a track array.
function activity(ts, hotRoom, tracks, opts = {}) {
  const rooms = mockLayout.rooms.map((r) => ({
    room: r.id,
    activity_index: r.id === hotRoom ? opts.heat ?? 70 : (opts.ambient ?? 5),
    occupancy_count: opts.occupancy?.[r.id] ?? (tracks.some((t) => t.room === r.id) ? 1 : 0),
  }));
  return {
    type: "room_activity",
    home_id: "rosewood",
    // Backendless demo data → the source badge reads "simulated" (BAT-34).
    source: "simulated",
    ts,
    rooms,
    tracks,
    tracking_degraded: !!opts.degraded,
  };
}

const A = (room, state = "normal", confidence = 0.86) => ({
  track_id: "A",
  label: "A",
  room,
  confidence,
  identity_confidence: 0.9,
  state,
  // Coarse vitals estimates (BAT-33): a steady ~15 brpm, posture floor while
  // under fall watch and upright otherwise. Optional in the contract; the demo
  // supplies them so the HUD's Respiration/Posture fields light up.
  respiration_bpm_estimate: 15,
  posture_estimate: state === "fall_watch" ? "floor" : "upright",
});
const guest = (n, room) => ({
  track_id: `guest-${n}`,
  label: `guest-${n}`,
  room,
  confidence: 0.55,
  identity_confidence: 0.0,
  state: "guest",
});

/**
 * The original scripted multi-room demo: idle → movement → guest → fall → recover.
 * One entry per 1 Hz tick (each entry is an array of WS messages emitted that tick).
 */
function demoScenario() {
  return [
    // resident pottering in the kitchen
    [activity(1, "kitchen", [A("kitchen")])],
    [activity(2, "kitchen", [A("kitchen")], { heat: 80 })],
    // moves to the living room (room_transition event)
    [
      {
        type: "event",
        event: "room_transition",
        home_id: "rosewood",
        track_id: "A",
        from_room: "kitchen",
        room: "living",
        ts: 3,
      },
      activity(3, "living", [A("living")]),
    ],
    [activity(4, "living", [A("living")], { heat: 55 })],
    // a visitor arrives — second (guest) track in the living room
    [activity(5, "living", [A("living"), guest(1, "living")], { heat: 60 })],
    [activity(6, "living", [A("living"), guest(1, "living")], { heat: 40 })],
    // dwell, then the resident goes to the bathroom
    [
      { type: "event", event: "dwell_start", home_id: "rosewood", track_id: "A", room: "living", dwell_secs: 30, ts: 7 },
      activity(7, "bathroom", [A("bathroom"), guest(1, "living")]),
    ],
    // a fall: A enters fall_watch, incident streams the transition
    [
      {
        type: "incident",
        home_id: "rosewood",
        incident_id: "inc-demo-1",
        node_id: 3,
        room: "bathroom",
        from_state: "PROBABLE",
        to_state: "CONFIRMED_FALL",
        kind: "confirmed_fall",
        ts: 8,
      },
      activity(8, "bathroom", [A("bathroom", "fall_watch"), guest(1, "living")], { heat: 15 }),
    ],
    [activity(9, "bathroom", [A("bathroom", "fall_watch"), guest(1, "living")], { heat: 10 })],
    // tracking confidence drops — degrade to room count badges
    [activity(10, "bathroom", [], { degraded: true, occupancy: { bathroom: 1, living: 1 } })],
    // recovered: back to normal, resident in the bedroom
    [
      {
        type: "incident",
        home_id: "rosewood",
        incident_id: "inc-demo-1",
        node_id: 3,
        room: "bathroom",
        from_state: "CONFIRMED_FALL",
        to_state: "NORMAL",
        kind: "transition",
        ts: 11,
      },
      activity(11, "bedroom", [A("bedroom")]),
    ],
    [activity(12, "bedroom", [A("bedroom")], { heat: 30 })],
  ];
}

/**
 * REAL captured walk — node 1 (Kitchen), 2026-06-12. The `activity_index` values
 * are ACTUAL, computed from a recorded ESP32 packet stream under the live feed's
 * ACTIVITY_REF=1500 / ACTIVITY_WINDOW_SECS=8. A person walks through the kitchen
 * then leaves: activity ramps to ~90 and decays smoothly, presence latches at 0.9
 * and ages out. Source: esp32-presence-node/data/captured_readings.csv. Unlike the
 * demo, this is a single-node presence reading — no respiration/heart, so the HUD
 * vitals show "—" (honest for these nodes).
 */
function kitchenWalkScenario() {
  const ai =    [93, 86, 71, 72, 81, 84, 81, 71, 60, 50, 45, 35, 21, 8, 1, 0, 0];
  const score = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.8, 0.7, 0.6, 0.5, 0.5, 0.5, 0.5, 0.5, 0.39, 0.29];
  return ai.map((heat, t) => {
    const present = score[t] >= 0.4; // TRACK_CONF_MIN
    // Wander within the kitchen (node ~0.25,0.25). u/v are 0..1 layout coords;
    // the renderer places the figure here instead of the room centre, so it moves
    // around the room. NOTE: real single-node data has no in-room position — this
    // previews what multi-node localization will look like.
    const u = 0.25 + 0.11 * Math.sin(t * 0.7);
    const v = 0.25 + 0.09 * Math.cos(t * 0.55);
    const tracks = present
      ? [{ track_id: "A", label: "A", room: "kitchen", u, v, confidence: score[t],
           // state stays in the documented enum {normal, fall_watch, guest};
           // posture is NOT a track state (single-node data can't infer it) and
           // the HUD renders state verbatim, so we keep it honest as "normal".
           identity_confidence: 0.9, state: "normal" }]
      : [];
    return [activity(t, "kitchen", tracks, { heat, ambient: 0 })];
  });
}

// Selectable mock scenarios. Pick one with VITE_MOCK_SCENARIO (see api.js).
const SCENARIOS = {
  demo: demoScenario,
  "kitchen-walk": kitchenWalkScenario,
};
export const MOCK_SCENARIOS = Object.keys(SCENARIOS);

/**
 * Looping scripted scenario, one entry per 1 Hz tick. `scenario` selects which
 * (default "demo"); unknown names fall back to the demo so callers never break.
 */
export function mockFrameStream(scenario = "demo") {
  return (SCENARIOS[scenario] || demoScenario)();
}
