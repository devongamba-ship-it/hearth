// Queue ordering (BAT-28 §Queue): P1 falls on top, unclaimed before claimed
// before resolved, oldest-first so the longest-waiting alert sits at the top.

import { describe, expect, it } from "vitest";
import { openIncidents, queueOrder } from "../queue.js";

const mk = (id, status, created_at, extra = {}) => ({
  id,
  home_name: "Home",
  room: "Kitchen",
  status,
  created_at,
  ...extra,
});

describe("queueOrder", () => {
  it("puts unclaimed before claimed before resolved", () => {
    const ordered = queueOrder([
      mk("resolved", "resolved", 100),
      mk("claimed", "claimed", 200),
      mk("active", "active", 300),
    ]);
    expect(ordered.map((i) => i.id)).toEqual(["active", "claimed", "resolved"]);
  });

  it("within the same status, oldest is on top (highest age)", () => {
    const ordered = queueOrder([
      mk("newer", "active", 500),
      mk("older", "active", 100),
    ]);
    expect(ordered.map((i) => i.id)).toEqual(["older", "newer"]);
  });

  it("ranks an explicitly lower-priority alert beneath a P1", () => {
    const ordered = queueOrder([
      mk("p2", "active", 100, { priority: "P2" }),
      mk("p1", "active", 500, { priority: "P1" }),
    ]);
    expect(ordered.map((i) => i.id)).toEqual(["p1", "p2"]);
  });

  it("does not mutate its input", () => {
    const input = [mk("a", "claimed", 1), mk("b", "active", 2)];
    queueOrder(input);
    expect(input.map((i) => i.id)).toEqual(["a", "b"]);
  });

  it("openIncidents drops resolved alerts", () => {
    const open = openIncidents([mk("a", "active", 1), mk("b", "resolved", 2)]);
    expect(open.map((i) => i.id)).toEqual(["a"]);
  });
});
