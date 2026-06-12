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

  // Live floor: a low-confidence (tracking_degraded) frame or any node that has
  // gone silent >10s (uncleared sensing_dropout) drops the badge to degraded.
  const degraded = Boolean(frame.tracking_degraded) || offlineCount > 0;
  if (degraded) {
    return offlineCount > 0
      ? { kind: "degraded", label: `degraded · ${offlineCount} offline`, offlineCount }
      : { kind: "degraded", label: "degraded" };
  }

  // Healthy live floor. N is the count of reporting nodes; the aggregated frame
  // exposes anonymous tracks (never node-level detail — privacy), so the badge
  // derives N from the track count, the per-target signal the feed provides.
  const nodeCount = nodesReporting(frame);
  return {
    kind: "live",
    label: `live · ${nodeCount} node${nodeCount === 1 ? "" : "s"}`,
    nodeCount,
  };
}

function nodesReporting(frame) {
  if (typeof frame.nodeCount === "number") return frame.nodeCount;
  return Array.isArray(frame.tracks) ? frame.tracks.length : 0;
}
