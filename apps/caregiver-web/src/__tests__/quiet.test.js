// The quiet-window countdown formatter (BAT-39): coarse "1h 29m" / "29m" / "45s".

import { describe, it, expect } from "vitest";
import { remaining } from "../format.js";

describe("remaining()", () => {
  it("renders sub-minute durations in seconds", () => {
    expect(remaining(45)).toBe("45s");
    expect(remaining(0)).toBe("0s");
  });

  it("rounds minutes up so a live countdown never flashes a stale value", () => {
    expect(remaining(61)).toBe("2m"); // 1m1s rounds up while still running
    expect(remaining(1800)).toBe("30m");
  });

  it("renders hours and minutes past an hour", () => {
    expect(remaining(7200)).toBe("2h 0m");
    expect(remaining(5340)).toBe("1h 29m");
  });

  it("never goes negative", () => {
    expect(remaining(-10)).toBe("0s");
  });
});
