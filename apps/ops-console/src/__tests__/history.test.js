// History model (CANONICAL UPDATE #5): one timeline for falls/wellness/system,
// resolved incidents appended with response time, 90-day display clamp, and a
// full-log export.

import { describe, expect, it } from "vitest";
import { auditLogJson, buildHistory, incidentToEvent } from "../history.js";

const NOW = 1718100000;
const resolved = {
  id: "r1",
  home_name: "Rosewood",
  room: "Kitchen",
  status: "resolved",
  created_at: NOW - 3600,
  claimed_at: NOW - 3540,
  disposition: "recovered",
  disposition_reason: "tripped, got up fine",
};
const old = { ...resolved, id: "old", created_at: NOW - 100 * 86400, claimed_at: NOW - 100 * 86400 + 60 };
const active = { id: "a1", home_name: "Rosewood", room: "Bathroom", status: "active", created_at: NOW - 60 };

describe("history model", () => {
  it("maps a resolved incident to an event with disposition + response time", () => {
    const e = incidentToEvent(resolved);
    expect(e.type).toBe("fall");
    expect(e.person).toBe("A"); // track label, never a name
    expect(e.outcome).toContain("Recovered");
    expect(e.outcome).toContain("responded 1m 0s");
  });

  it("merges falls with wellness/system events, newest first", () => {
    const wellness = { ts: NOW - 30, type: "wellness", description: "No morning activity", room: "Kitchen", person: "A", outcome: "checked" };
    const events = buildHistory([resolved, active], [wellness], { now: NOW });
    expect(events[0].ts).toBeGreaterThanOrEqual(events[1].ts);
    expect(events.map((e) => e.type)).toContain("wellness");
  });

  it("clamps the displayed window to 90 days", () => {
    const events = buildHistory([resolved, old], [], { now: NOW });
    expect(events.map((e) => e.id)).toContain("r1");
    expect(events.map((e) => e.id)).not.toContain("old");
  });

  it("export includes the full log, beyond the retention window", () => {
    const json = auditLogJson([resolved, old], []);
    expect(json).toContain("old");
    expect(json).toContain("r1");
  });
});
