// Demo helpers (BAT-43): the `?demo` gate and the scenario list.

import { describe, expect, it } from "vitest";
import { DEMO_SCENARIOS, isDemoEnabled } from "../demo.js";

describe("isDemoEnabled", () => {
  it("is on only when ?demo is present", () => {
    expect(isDemoEnabled("?demo")).toBe(true);
    expect(isDemoEnabled("demo")).toBe(true); // tolerates a missing leading ?
    expect(isDemoEnabled("?x=1&demo")).toBe(true);
    expect(isDemoEnabled("?demo=1")).toBe(true);
  });

  it("is off by default and for unrelated params", () => {
    expect(isDemoEnabled("")).toBe(false);
    expect(isDemoEnabled("?foo=1")).toBe(false);
    expect(isDemoEnabled("?demonstrate=1")).toBe(false);
  });
});

describe("DEMO_SCENARIOS", () => {
  it("offers exactly the four backend scenarios", () => {
    expect(DEMO_SCENARIOS.map((s) => s.key)).toEqual([
      "fall_confirmed",
      "fall_recovered",
      "multi_occupancy",
      "quiet_morning",
    ]);
  });

  it("each scenario has a human label and a hint", () => {
    for (const s of DEMO_SCENARIOS) {
      expect(s.label).toBeTruthy();
      expect(s.hint).toBeTruthy();
    }
  });
});
