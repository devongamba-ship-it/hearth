// Status-card composition through a simulated fall → claim (BAT-25 acceptance).
//
// This is the headline behaviour: the card starts green ("All good"), turns red
// the instant a fall cascade opens, and shows "Claimed by …" once a caregiver
// claims it — with the escalation incident always overriding the ingest status.

import { describe, it, expect } from "vitest";
import { composeStatus, isClaimable, LEVELS, STATES } from "../status.js";

const allGood = {
  state: "all_good",
  headline: "All good",
  detail: "Last activity: Kitchen, 2 min ago.",
};

function activeCascade() {
  return [{ id: "esc-1", room: "Kitchen", status: "active", created_at: 1000 }];
}
function claimed(by) {
  return [{ id: "esc-1", room: "Kitchen", status: "claimed", claimed_by: by, created_at: 1000 }];
}

describe("composeStatus", () => {
  it("is green and 'All good' with no incident", () => {
    const v = composeStatus(allGood, []);
    expect(v.level).toBe(LEVELS.GREEN);
    expect(v.state).toBe(STATES.ALL_GOOD);
    expect(v.headline).toBe("All good");
    expect(isClaimable(v)).toBe(false);
  });

  it("turns red and claimable the moment a cascade opens", () => {
    const v = composeStatus(allGood, activeCascade());
    expect(v.level).toBe(LEVELS.RED);
    expect(v.state).toBe(STATES.CASCADE);
    expect(v.headline).toMatch(/possible fall/i);
    expect(isClaimable(v)).toBe(true);
  });

  it("shows 'Claimed by <name>' and is no longer claimable once claimed", () => {
    const v = composeStatus(allGood, claimed("Dana"));
    expect(v.level).toBe(LEVELS.RED);
    expect(v.state).toBe(STATES.CLAIMED);
    expect(v.headline).toBe("Claimed by Dana");
    expect(isClaimable(v)).toBe(false);
  });

  it("an active incident overrides even a healthy ingest status", () => {
    const v = composeStatus(allGood, activeCascade());
    expect(v.state).toBe(STATES.CASCADE); // not all_good
  });

  it("maps an unconfirmed fall to amber 'checking in'", () => {
    const v = composeStatus(
      { state: "checking_in", headline: "Quiet — checking in", detail: "..." },
      [],
    );
    expect(v.level).toBe(LEVELS.AMBER);
    expect(v.state).toBe(STATES.CHECKING_IN);
  });

  it("falls back to amber 'Connecting' before the first status arrives", () => {
    const v = composeStatus(null, []);
    expect(v.level).toBe(LEVELS.AMBER);
    expect(v.headline).toMatch(/connecting/i);
  });
});
