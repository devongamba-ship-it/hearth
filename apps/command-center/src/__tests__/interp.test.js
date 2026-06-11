import { describe, it, expect } from "vitest";
import { lerp, smoothstep, interpolateFigures } from "../lib/interp.js";

const fig = (id, x, z) => ({ id, guest: false, position: { x, z } });

describe("lerp / smoothstep", () => {
  it("lerp interpolates linearly", () => {
    expect(lerp(0, 10, 0)).toBe(0);
    expect(lerp(0, 10, 0.5)).toBe(5);
    expect(lerp(0, 10, 1)).toBe(10);
  });

  it("smoothstep eases and clamps to [0,1]", () => {
    expect(smoothstep(0)).toBe(0);
    expect(smoothstep(1)).toBe(1);
    expect(smoothstep(0.5)).toBe(0.5);
    expect(smoothstep(-1)).toBe(0);
    expect(smoothstep(2)).toBe(1);
  });
});

describe("interpolateFigures", () => {
  const prev = [fig("A", 0, 0)];
  const next = [fig("A", 10, 0)];

  it("at alpha 0 sits at the previous position", () => {
    expect(interpolateFigures(prev, next, 0)[0].position.x).toBeCloseTo(0);
  });

  it("at alpha 1 reaches the target", () => {
    expect(interpolateFigures(prev, next, 1)[0].position.x).toBeCloseTo(10);
  });

  it("eases between (smoothstep, not linear)", () => {
    const mid = interpolateFigures(prev, next, 0.5)[0].position.x;
    expect(mid).toBeCloseTo(5); // smoothstep(0.5) === 0.5
    const quarter = interpolateFigures(prev, next, 0.25)[0].position.x;
    expect(quarter).toBeLessThan(2.5); // eased in: slower than linear at 0.25
  });

  it("snaps figures that just appeared (no prev to ease from)", () => {
    const out = interpolateFigures([], [fig("G", 7, 7)], 0.2);
    expect(out[0].position).toEqual({ x: 7, z: 7 });
  });

  it("drops figures absent from next", () => {
    const out = interpolateFigures([fig("A", 0, 0), fig("B", 1, 1)], [fig("A", 2, 2)], 1);
    expect(out.map((f) => f.id)).toEqual(["A"]);
  });
});
