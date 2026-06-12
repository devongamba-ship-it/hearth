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
    activity_index: r.id === hotRoom ? opts.heat ?? 70 : 5,
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
 * Looping scripted scenario, one entry per 1 Hz tick (each entry is an array of
 * WS messages emitted that tick). The render loop interpolates between ticks.
 */
export function mockFrameStream() {
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
