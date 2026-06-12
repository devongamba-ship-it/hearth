// Data-source badge derivation (BAT-34). The four states match the
// dashboard-mockup reference: simulated / live / degraded / feed offline.
// Acceptance-criterion unit tests for the source-label logic.

import { describe, it, expect } from "vitest";
import { deriveSourceBadge } from "../lib/source.js";

function frame(over = {}) {
  return {
    source: "live",
    home_id: "rosewood",
    tracking_degraded: false,
    nodeCount: 0,
    ...over,
  };
}

describe("deriveSourceBadge (BAT-34)", () => {
  it("reads `simulated` when the feed is fed by packet_sim", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame({ source: "simulated" }) });
    expect(badge).toMatchObject({ kind: "simulated", label: "simulated" });
  });

  it("falls back to simulated for the demo home when source is absent", () => {
    const badge = deriveSourceBadge({
      connected: true,
      frame: { home_id: "Rosewood", tracking_degraded: false, nodeCount: 1 },
    });
    expect(badge.kind).toBe("simulated");
  });

  it("reads `live · N nodes` from the reporting node count", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame({ nodeCount: 3 }) });
    expect(badge).toMatchObject({ kind: "live", label: "live · 3 nodes", nodeCount: 3 });
  });

  it("derives the node count from a tracks array too", () => {
    const badge = deriveSourceBadge({
      connected: true,
      frame: { source: "live", tracks: [{ track_id: "A" }] },
    });
    expect(badge.label).toBe("live · 1 node");
  });

  it("counts reporting NODES from the frame, not the track/person count", () => {
    // Empty home, 4 healthy nodes: 0 tracks but still live · 4 nodes.
    const badge = deriveSourceBadge({
      connected: true,
      frame: { source: "live", tracks: [], nodes_reporting: 4 },
    });
    expect(badge).toMatchObject({ kind: "live", label: "live · 4 nodes" });
  });

  it("stays `degraded` for a SUSTAINED outage via the frame's nodes_offline", () => {
    for (const ts of [1001, 1010, 1060]) {
      const badge = deriveSourceBadge({
        connected: true,
        frame: frame({ ts, nodes_offline: [3], nodes_reporting: 3 }),
      });
      expect(badge).toMatchObject({ kind: "degraded", label: "degraded · 1 offline" });
    }
  });

  it("reads `degraded · N offline` when a node has gone silent", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame(), offlineCount: 1 });
    expect(badge).toMatchObject({ kind: "degraded", label: "degraded · 1 offline" });
  });

  it("reads `degraded` when the frame's tracking_degraded flag is set", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame({ tracking_degraded: true }) });
    expect(badge).toMatchObject({ kind: "degraded", label: "degraded" });
  });

  it("reads `feed offline` when the WS is closed", () => {
    expect(deriveSourceBadge({ connected: false, frame: frame() }).kind).toBe("offline");
  });

  it("reads `feed offline` before the first frame arrives", () => {
    expect(deriveSourceBadge({ connected: true, frame: null }).kind).toBe("offline");
  });

  it("keeps `simulated` even while degraded (source is the primary axis)", () => {
    const badge = deriveSourceBadge({
      connected: true,
      frame: frame({ source: "simulated", tracking_degraded: true }),
      offlineCount: 2,
    });
    expect(badge.kind).toBe("simulated");
  });
});
