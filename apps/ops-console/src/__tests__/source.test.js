// Data-source badge derivation (BAT-34). The four states match the
// dashboard-mockup reference: simulated / live / degraded / feed offline.
// These are the acceptance-criterion unit tests for the source-label logic.

import { describe, expect, it } from "vitest";
import { deriveSourceBadge, foldDropouts } from "../source.js";

function frame(over = {}) {
  return {
    type: "room_activity",
    home_id: "rosewood",
    source: "live",
    ts: 1000,
    tracking_degraded: false,
    tracks: [],
    ...over,
  };
}

describe("deriveSourceBadge (BAT-34)", () => {
  it("reads `simulated` when the feed is fed by packet_sim", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame({ source: "simulated" }) });
    expect(badge.kind).toBe("simulated");
    expect(badge.label).toBe("simulated");
  });

  it("falls back to simulated for the demo home when source is absent", () => {
    const { source, ...noSource } = frame({ home_id: "Rosewood" });
    expect(source).toBe("live"); // (only proving we stripped it)
    const badge = deriveSourceBadge({ connected: true, frame: noSource });
    expect(badge.kind).toBe("simulated");
  });

  it("reads `live · N nodes` with the reporting node count", () => {
    const tracks = [{ track_id: "A" }, { track_id: "guest-1" }, { track_id: "guest-2" }];
    const badge = deriveSourceBadge({ connected: true, frame: frame({ tracks }) });
    expect(badge.kind).toBe("live");
    expect(badge.label).toBe("live · 3 nodes");
    expect(badge.nodeCount).toBe(3);
  });

  it("singularises the node count", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame({ tracks: [{ track_id: "A" }] }) });
    expect(badge.label).toBe("live · 1 node");
  });

  it("counts reporting NODES from the frame, not the track/person count", () => {
    // Empty home, all 4 nodes healthy: 0 tracks but the floor is live · 4 nodes.
    const badge = deriveSourceBadge({
      connected: true,
      frame: frame({ tracks: [], nodes_reporting: 4 }),
    });
    expect(badge).toMatchObject({ kind: "live", label: "live · 4 nodes" });
  });

  it("stays `degraded` for a SUSTAINED outage via the frame's nodes_offline", () => {
    // The badge reads the authoritative offline set on every frame, so a node
    // that stays down keeps the badge degraded tick after tick (the bug was it
    // flickering back to live after the one-shot dropout event).
    for (const ts of [1001, 1010, 1060]) {
      const badge = deriveSourceBadge({
        connected: true,
        frame: frame({ ts, nodes_offline: [3], nodes_reporting: 3 }),
      });
      expect(badge).toMatchObject({ kind: "degraded", label: "degraded · 1 offline" });
    }
  });

  it("reads `degraded · N offline` when a node has gone silent", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame(), offlineCount: 2 });
    expect(badge.kind).toBe("degraded");
    expect(badge.label).toBe("degraded · 2 offline");
  });

  it("reads `degraded` when the frame's tracking_degraded flag is set", () => {
    const badge = deriveSourceBadge({ connected: true, frame: frame({ tracking_degraded: true }) });
    expect(badge.kind).toBe("degraded");
    expect(badge.label).toBe("degraded");
  });

  it("reads `feed offline` when the WS is closed", () => {
    const badge = deriveSourceBadge({ connected: false, frame: frame() });
    expect(badge.kind).toBe("offline");
    expect(badge.label).toBe("feed offline");
  });

  it("reads `feed offline` before the first frame arrives", () => {
    const badge = deriveSourceBadge({ connected: true, frame: null });
    expect(badge.kind).toBe("offline");
  });

  it("keeps `simulated` even while degraded (source is the primary axis)", () => {
    const badge = deriveSourceBadge({
      connected: true,
      frame: frame({ source: "simulated", tracking_degraded: true }),
      offlineCount: 1,
    });
    expect(badge.kind).toBe("simulated");
  });
});

describe("foldDropouts (offline-node lifecycle)", () => {
  const drop = (node_id, ts) => ({ type: "event", event: "sensing_dropout", node_id, ts });
  const tick = (over = {}) => ({ type: "room_activity", tracking_degraded: false, ts: 1000, ...over });

  it("adds a node on sensing_dropout", () => {
    const d = foldDropouts(new Map(), drop(2, 500));
    expect(d.size).toBe(1);
    expect(d.has(2)).toBe(true);
  });

  it("clears on the next healthy frame newer than the dropout", () => {
    let d = foldDropouts(new Map(), drop(2, 500));
    d = foldDropouts(d, tick({ ts: 501 }));
    expect(d.size).toBe(0);
  });

  it("does NOT clear on a same-tick frame (shared ts)", () => {
    let d = foldDropouts(new Map(), drop(2, 500));
    d = foldDropouts(d, tick({ ts: 500 }));
    expect(d.size).toBe(1);
  });

  it("does NOT clear while the frame is still tracking_degraded", () => {
    let d = foldDropouts(new Map(), drop(2, 500));
    d = foldDropouts(d, tick({ ts: 999, tracking_degraded: true }));
    expect(d.size).toBe(1);
  });

  it("ignores unrelated messages", () => {
    const d0 = new Map();
    expect(foldDropouts(d0, { type: "incident" })).toBe(d0);
    expect(foldDropouts(d0, null)).toBe(d0);
  });
});
