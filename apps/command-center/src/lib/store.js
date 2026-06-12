// Pure scene store / reducer (BAT-29).
//
// Folds the live WS messages (room_activity / event / incident, per BAT-27 v0.2)
// into a render-ready scene state. Pure and synchronous: the WS driver calls
// `applyMessage(state, msg, scene)` for each message and feeds the result to the
// renderer. Keeping it pure makes the whole frame→scene path unit-testable with
// no socket, no clock, and no Three.js.
//
// State shape:
//   {
//     homeId,
//     rooms: [{ room, activity_index, occupancy_count, tint }],
//     figures: [...tracksToFigures output],
//     tracking_degraded: bool,
//     occupancyBadges: [{ room, count }],   // shown when degraded (no tracks)
//     fallWatch: bool,                       // any track / incident in fall watch
//     incident: { incident_id, room, state } | null,
//     events: [...recent ticker items, newest first],
//     hud: { ... },
//     lastTs: number | null,
//     source: "simulated" | "live" | null,   // data-source label off the frame
//     offlineNodes: [nodeId],                 // nodes with an uncleared dropout
//     lastDropoutTs: number | null,           // newest dropout ts (for clearing)
//   }

import { tracksToFigures } from "./figures.js";
import { tintFor } from "./heat.js";

export const MAX_EVENTS = 12; // ticker keeps this many, newest first

// Fall-machine states that mean "this person is under fall watch" (mirrors the
// backend _WATCH_STATES; the feed already tags tracks, this is for `incident`).
const WATCH_STATES = new Set([
  "SUSPECTED",
  "PROBABLE",
  "CHECK_IN_ACTIVE",
  "CONFIRMED_FALL",
]);

export function initialState(scene) {
  return {
    homeId: scene?.homeId ?? "home",
    rooms: [],
    figures: [],
    tracking_degraded: false,
    occupancyBadges: [],
    fallWatch: false,
    incident: null,
    events: [],
    hud: emptyHud(),
    lastTs: null,
    // Data-source badge inputs (BAT-34): the label the feed stamps on each frame
    // and the set of nodes currently flagged offline by a sensing_dropout. The
    // offline set clears on the next healthy frame (the feed emits no per-node
    // recovery event — privacy: aggregated frames only).
    source: null,
    offlineNodes: [],
    lastDropoutTs: null,
  };
}

function emptyHud() {
  return {
    rooms: [],
    resident: null,
    // Respiration and posture are coarse, smoothed *estimates* the feed may
    // attach to the resident track (BAT-33) — still aggregates, never raw or
    // per-second. They are optional: null until the feed supplies them, and the
    // HUD renders "—" for a null rather than fabricating a vital sign.
    respiration: null,
    posture: null,
    motionEnergy: null,
    confidence: null,
    fallWatch: false,
  };
}

function roomNameLookup(scene) {
  const names = new Map((scene?.rooms ?? []).map((r) => [r.id, r.name]));
  return (id) => names.get(id) ?? id;
}

function buildHud(rooms, figures, scene, fallWatch) {
  const nameOf = roomNameLookup(scene);
  // The resident track is the non-guest figure with the highest identity
  // confidence (v1: the single known track "A").
  const resident = figures
    .filter((f) => !f.guest)
    .sort((a, b) => b.identityConfidence - a.identityConfidence)[0];
  const residentRoom = resident
    ? rooms.find((r) => r.room === resident.room)
    : null;
  return {
    rooms: rooms.map((r) => ({
      room: r.room,
      name: nameOf(r.room),
      activity_index: r.activity_index,
      occupancy_count: r.occupancy_count,
    })),
    resident: resident
      ? {
          label: resident.label,
          room: resident.room,
          roomName: nameOf(resident.room),
          confidence: resident.confidence,
          identityConfidence: resident.identityConfidence,
          state: resident.state,
        }
      : null,
    // Coarse vitals estimates off the resident track when the feed supplies them
    // (BAT-33); null → the HUD shows "—". Never fabricated locally.
    respiration: resident ? resident.respiration ?? null : null,
    posture: resident ? resident.posture ?? null : null,
    // Motion energy proxy: the resident's room activity index (the motion-derived
    // metric the feed actually provides). Null when the resident isn't located.
    motionEnergy: residentRoom ? residentRoom.activity_index : null,
    confidence: resident ? resident.confidence : null,
    fallWatch,
  };
}

function applyRoomActivity(state, msg, scene) {
  const rooms = (msg.rooms ?? []).map((r) => ({
    room: r.room,
    activity_index: r.activity_index ?? 0,
    occupancy_count: r.occupancy_count ?? 0,
    tint: tintFor(r.activity_index ?? 0),
  }));
  const degraded = !!msg.tracking_degraded;
  const figures = degraded ? [] : tracksToFigures(msg.tracks ?? [], scene);
  // When tracking is degraded there are no positional tracks — clients degrade
  // to per-room count badges (canonical update §4).
  const occupancyBadges = degraded
    ? rooms.filter((r) => r.occupancy_count > 0).map((r) => ({
        room: r.room,
        count: r.occupancy_count,
      }))
    : [];
  // Fall watch from the track states (the incident stream also sets it).
  const trackFallWatch = figures.some((f) => f.fallen);
  const fallWatch = trackFallWatch || isIncidentWatching(state.incident);
  // Data-source label (BAT-34) + offline-node clearing. A healthy (non-degraded)
  // frame strictly newer than every recorded dropout means the mesh recovered —
  // clear the offline set (the feed emits no per-node recovery event).
  const frameTs = msg.ts ?? state.lastTs;
  const recovered =
    state.offlineNodes.length > 0 &&
    !degraded &&
    frameTs != null &&
    state.lastDropoutTs != null &&
    frameTs > state.lastDropoutTs;
  return {
    ...state,
    rooms,
    figures,
    tracking_degraded: degraded,
    occupancyBadges,
    fallWatch,
    source: msg.source ?? state.source,
    offlineNodes: recovered ? [] : state.offlineNodes,
    lastDropoutTs: recovered ? null : state.lastDropoutTs,
    hud: buildHud(rooms, figures, scene, fallWatch),
    lastTs: msg.ts ?? state.lastTs,
  };
}

function isIncidentWatching(incident) {
  return !!incident && WATCH_STATES.has(incident.state);
}

function pushEvent(events, item) {
  return [item, ...events].slice(0, MAX_EVENTS);
}

function applyEvent(state, msg) {
  const item = {
    kind: msg.event, // room_transition | dwell_start | sensing_dropout
    room: msg.room ?? null,
    fromRoom: msg.from_room ?? null,
    trackId: msg.track_id ?? null,
    nodeId: msg.node_id ?? null,
    ts: msg.ts ?? state.lastTs,
    detail: msg,
  };
  const events = pushEvent(state.events, item);
  // A node going silent >10s flags it offline for the data-source badge (BAT-34).
  if (msg.event === "sensing_dropout" && msg.node_id != null) {
    const offlineNodes = state.offlineNodes.includes(msg.node_id)
      ? state.offlineNodes
      : [...state.offlineNodes, msg.node_id];
    const lastDropoutTs = Math.max(state.lastDropoutTs ?? -Infinity, msg.ts ?? 0);
    return { ...state, events, offlineNodes, lastDropoutTs };
  }
  return { ...state, events };
}

function applyIncident(state, msg, scene) {
  const watching = WATCH_STATES.has(msg.to_state);
  const incident = watching
    ? { incident_id: msg.incident_id, room: msg.room, state: msg.to_state }
    : null;
  const item = {
    kind: "incident",
    room: msg.room ?? null,
    nodeId: msg.node_id ?? null,
    fromState: msg.from_state,
    toState: msg.to_state,
    incidentKind: msg.kind,
    ts: msg.ts ?? state.lastTs,
    detail: msg,
  };
  const fallWatch = watching || state.figures.some((f) => f.fallen);
  return {
    ...state,
    incident,
    fallWatch,
    hud: { ...state.hud, fallWatch },
    events: pushEvent(state.events, item),
  };
}

/** Fold one WS message into scene state. Unknown types pass through unchanged. */
export function applyMessage(state, msg, scene) {
  if (!msg || typeof msg !== "object") return state;
  switch (msg.type) {
    case "room_activity":
      return applyRoomActivity(state, msg, scene);
    case "event":
      return applyEvent(state, msg);
    case "incident":
      return applyIncident(state, msg, scene);
    default:
      return state;
  }
}
