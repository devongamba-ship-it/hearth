// Data-source badge derivation (BAT-34).
//
// One always-visible badge tells the operator at a glance whether the floor is
// driven by *simulated* packets or *real silicon*, and surfaces feed health —
// so the calibration-day flip from packet_sim to live ESP32 nodes visibly
// confirms itself and a node dropping out is obvious. The four states match the
// `dashboard-mockup.html` reference: simulated / live / degraded / offline.
//
// Pure: this derives the badge from observable feed state only (the latest
// room_activity frame, the WS connection flag, and the count of nodes currently
// flagged offline). No raw readings involved — `source` is a coarse label the
// ingest feed stamps on the frame (privacy invariant holds).

// The demo home id. When a frame omits `source` (older ingest, or the mock
// fixtures), a frame from the demo home is treated as simulated — that floor is
// never real silicon.
export const DEMO_HOME = "rosewood";

function isDemoHome(homeId) {
  return typeof homeId === "string" && homeId.toLowerCase() === DEMO_HOME;
}

/**
 * Derive the data-source badge from feed state.
 *
 * @param {object}  args
 * @param {boolean} args.connected     WS open (false → feed offline)
 * @param {object}  [args.frame]       latest room_activity frame (null until first)
 * @param {number}  [args.offlineCount] nodes with an uncleared sensing_dropout
 * @returns {{kind: "simulated"|"live"|"degraded"|"offline", label: string,
 *           nodeCount?: number, offlineCount?: number}}
 */
export function deriveSourceBadge({ connected, frame, offlineCount = 0 } = {}) {
  // WS closed/erroring, or no frame seen yet — we can't claim a live floor.
  if (!connected || !frame) {
    return { kind: "offline", label: "feed offline" };
  }

  // Source is the primary axis: the simulator is its own mode (the headline job
  // of this badge is the sim↔silicon distinction), so it wins over degraded.
  const source =
    frame.source ?? (isDemoHome(frame.home_id) ? "simulated" : "live");
  if (source === "simulated") {
    return { kind: "simulated", label: "simulated" };
  }

  // Live floor: a low-confidence (tracking_degraded) frame or any node currently
  // silent >10s drops the badge to degraded. Offline count comes from the
  // frame's authoritative nodes_offline (persists for the whole outage), falling
  // back to the client-reconstructed offlineCount only for older feeds.
  const offline = Array.isArray(frame.nodes_offline)
    ? frame.nodes_offline.length
    : offlineCount;
  const degraded = Boolean(frame.tracking_degraded) || offline > 0;
  if (degraded) {
    return offline > 0
      ? { kind: "degraded", label: `degraded · ${offline} offline`, offlineCount: offline }
      : { kind: "degraded", label: "degraded" };
  }

  // Healthy live floor. N = the count of reporting SENSORS (nodes), which the
  // backend stamps on the frame — NOT the track/person count (an empty home has
  // 0 tracks but its nodes are still live). A coarse node count is aggregate,
  // not node-level detail, so the privacy envelope holds.
  const nodeCount = nodesReporting(frame);
  return {
    kind: "live",
    label: `live · ${nodeCount} node${nodeCount === 1 ? "" : "s"}`,
    nodeCount,
  };
}

function nodesReporting(frame) {
  if (typeof frame.nodes_reporting === "number") return frame.nodes_reporting;
  if (typeof frame.nodeCount === "number") return frame.nodeCount;  // legacy
  return Array.isArray(frame.tracks) ? frame.tracks.length : 0;     // oldest feeds
}

/**
 * Fold one WS message into the set of nodes currently flagged offline (a
 * `Map<nodeId, dropoutTs>`). A `sensing_dropout` event marks its node offline.
 *
 * Clearing: the aggregated feed emits NO per-node recovery event (privacy — no
 * node-level frames leave the home), so "the mesh produced a healthy frame
 * after the drop" is the honest recovery signal: a non-degraded room_activity
 * frame strictly newer than every recorded dropout clears the set. Strict-newer
 * means a dropout reported in the same tick as a healthy frame (they share `ts`)
 * survives that tick rather than clearing the instant it is announced.
 *
 * Pure — returns the same Map reference when nothing changes (cheap to compare).
 */
export function foldDropouts(dropouts, msg) {
  if (!msg || typeof msg !== "object") return dropouts;
  if (msg.type === "event" && msg.event === "sensing_dropout" && msg.node_id != null) {
    const next = new Map(dropouts);
    next.set(msg.node_id, msg.ts ?? 0);
    return next;
  }
  if (msg.type === "room_activity" && dropouts.size > 0 && !msg.tracking_degraded) {
    const frameTs = msg.ts ?? 0;
    let newest = -Infinity;
    for (const ts of dropouts.values()) newest = Math.max(newest, ts);
    if (frameTs > newest) return new Map();
  }
  return dropouts;
}
