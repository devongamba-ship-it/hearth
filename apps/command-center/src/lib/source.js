// Data-source badge derivation (BAT-34).
//
// One always-visible badge in the HUD strip tells the operator whether the
// floor is driven by *simulated* packets or *real silicon*, plus feed health —
// so the calibration-day flip from packet_sim to live ESP32 nodes visibly
// confirms itself and a node dropping out is obvious. The four states match the
// `dashboard-mockup.html` reference: simulated / live / degraded / offline.
//
// Pure: derived from observable feed state only (latest frame fields, the WS
// connection flag, and the count of nodes currently flagged offline). `source`
// is a coarse label the ingest feed stamps on the frame — no raw readings.

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
 * @param {boolean} args.connected      WS open (false → feed offline)
 * @param {object}  [args.frame]        latest frame fields { source, home_id,
 *                                       tracking_degraded, nodeCount|tracks }
 *                                       (null until the first frame)
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
