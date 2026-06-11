// Alert copy principles (§4.1) + identity honesty — the BAT-28 CANONICAL UPDATE
// requirements that bind every ops surface. These are unit-tested directly so
// the rules can't regress no matter which screen renders them.

import { describe, expect, it } from "vitest";
import {
  STATE_CODES,
  codeChip,
  hasJargon,
  identityCaption,
  primaryCopy,
  roomPresenceCaption,
  trackColor,
  trackSlot,
} from "../copy.js";

describe("alert copy principles (§4.1)", () => {
  it("renders the canonical example exactly", () => {
    const text = primaryCopy({ state: "SUSPECTED", confidence: 0.91, trackLabel: "A" });
    expect(text).toBe(
      "Possible fall — movement matched a fall pattern (91%) and resident A appears to be on the floor",
    );
  });

  it("puts the technical state + confidence in the code chip", () => {
    expect(codeChip({ state: "SUSPECTED", confidence: 0.91 })).toBe("SUSPECTED · 0.91");
    expect(codeChip({ state: "CONFIRMED_FALL" })).toBe("CONFIRMED_FALL");
  });

  it("keeps internal jargon and raw state codes out of primary copy", () => {
    for (const state of STATE_CODES) {
      const text = primaryCopy({ state, confidence: 0.9, trackLabel: "A" });
      expect(hasJargon(text), `"${text}" leaked jargon`).toBe(false);
      // No raw state code (e.g. "CONFIRMED_FALL", "CHECK_IN_ACTIVE") in the line.
      for (const code of STATE_CODES) {
        expect(text.includes(code)).toBe(false);
      }
    }
  });

  it("shows a number only where it helps triage (suspected/probable)", () => {
    expect(primaryCopy({ state: "SUSPECTED", confidence: 0.91, trackLabel: "A" })).toContain("(91%)");
    expect(primaryCopy({ state: "CONFIRMED_FALL", confidence: 0.99, trackLabel: "A" })).not.toContain("%");
  });

  it("hasJargon flags banned terms and state codes", () => {
    expect(hasJargon("the cascade step fired")).toBe(true);
    expect(hasJargon("reached terminus")).toBe(true);
    expect(hasJargon("now in CONFIRMED_FALL")).toBe(true);
    expect(hasJargon("Fall confirmed and resident A appears to be on the floor")).toBe(false);
  });
});

describe("identity honesty", () => {
  it("labels tracks, never names, and discloses the heuristic + confidence", () => {
    const caption = identityCaption({ label: "A", identity_confidence: 0.8 });
    expect(caption).toBe("A (identity heuristic · 0.80)");
    // A guest track makes no identity claim.
    expect(identityCaption({ label: "guest-1", identity_confidence: 0 })).toBe(
      "guest-1 (identity heuristic)",
    );
  });

  it("primary copy uses the track label, not a name", () => {
    const text = primaryCopy({ state: "CONFIRMED_FALL", trackLabel: "B" });
    expect(text).toContain("resident B");
    expect(text).not.toMatch(/Dana|Marcus|Mom|Margaret/);
  });

  it("summarises room occupancy as '2 present · A, B'", () => {
    expect(roomPresenceCaption([{ label: "A" }, { label: "B" }])).toBe("2 present · A, B");
    expect(roomPresenceCaption([])).toBe("empty");
  });

  it("gives A a teal halo and B a violet halo", () => {
    expect(trackColor("A")).toBe("#14b8a6");
    expect(trackColor("B")).toBe("#8b5cf6");
    expect(trackColor("guest-1")).not.toBe(trackColor("A"));
  });

  it("assigns deterministic, distinct offset slots to co-located tracks", () => {
    expect(trackSlot(0)).toEqual(trackSlot(0)); // deterministic
    expect(trackSlot(0)).not.toEqual(trackSlot(1)); // distinct slots
  });
});
