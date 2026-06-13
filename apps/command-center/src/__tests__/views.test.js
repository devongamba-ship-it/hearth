import { describe, it, expect } from "vitest";
import { cameraFor, VIEWS, GROUND_EYE_HEIGHT } from "../lib/views.js";

describe("cameraFor", () => {
  it("exposes the camera views", () => {
    expect(VIEWS).toEqual(["birdseye", "ground", "orbit", "field"]);
  });

  it("field view is a low oblique orbit that frames the floor", () => {
    const a = cameraFor("field", { orbitAngle: 0 });
    const b = cameraFor("field", { orbitAngle: Math.PI });
    expect(a.position).not.toEqual(b.position); // orbits
    expect(a.position[1]).toBeGreaterThan(0);
    expect(a.position[1]).toBeLessThan(cameraFor("orbit", { orbitAngle: 0 }).position[1]); // lower than orbit
  });

  it("bird's eye looks straight down from above the centre", () => {
    const c = cameraFor("birdseye");
    expect(c.position[1]).toBeGreaterThan(0);
    expect(c.lookAt).toEqual([0, 0, 0]);
    expect(c.position[0]).toBeCloseTo(0);
  });

  it("ground view sits at eye height and tracks the target", () => {
    const target = { x: 3, z: -2 };
    const c = cameraFor("ground", { target, orbitAngle: 0 });
    expect(c.position[1]).toBeCloseTo(GROUND_EYE_HEIGHT + 0.4); // just above eye line, pulled back
    expect(c.lookAt[0]).toBeCloseTo(target.x);
    expect(c.lookAt[2]).toBeCloseTo(target.z);
    // camera is offset from, but near, the resident
    const d = Math.hypot(c.position[0] - target.x, c.position[2] - target.z);
    expect(d).toBeGreaterThan(0);
    expect(d).toBeLessThan(5);
  });

  it("orbit circles the centre as the angle advances", () => {
    const a = cameraFor("orbit", { orbitAngle: 0 });
    const b = cameraFor("orbit", { orbitAngle: Math.PI / 2 });
    expect(a.position).not.toEqual(b.position);
    const ra = Math.hypot(a.position[0], a.position[2]);
    const rb = Math.hypot(b.position[0], b.position[2]);
    expect(ra).toBeCloseTo(rb); // same radius, different angle
    expect(a.lookAt).toEqual([0, 0, 0]);
  });

  it("defaults unknown view to an orbit-style camera", () => {
    const c = cameraFor("???", { orbitAngle: 0 });
    expect(c.lookAt).toEqual([0, 0, 0]);
    expect(c.position[1]).toBeGreaterThan(0);
  });
});
