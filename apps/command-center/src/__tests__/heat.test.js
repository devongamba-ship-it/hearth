import { describe, it, expect } from "vitest";
import { tintFor } from "../lib/heat.js";

describe("tintFor", () => {
  it("is cool and faint when idle", () => {
    const t = tintFor(0);
    expect(t.color).toBe("#14534f");
    expect(t.opacity).toBeCloseTo(0.08);
  });

  it("warms and brightens with activity", () => {
    const hot = tintFor(100);
    expect(hot.color).toBe("#f59e0b");
    expect(hot.opacity).toBeGreaterThan(tintFor(0).opacity);
  });

  it("clamps out-of-range indices", () => {
    expect(tintFor(-50)).toEqual(tintFor(0));
    expect(tintFor(500)).toEqual(tintFor(100));
  });

  it("produces a valid hex colour", () => {
    expect(tintFor(42).color).toMatch(/^#[0-9a-f]{6}$/);
  });
});
