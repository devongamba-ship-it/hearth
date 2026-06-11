import { describe, it, expect } from "vitest";
import { layoutToScene, roomCenter, toWorld, FLOOR_SIZE } from "../lib/layout.js";
import { mockLayout } from "../mockFrames.js";

describe("layoutToScene", () => {
  const scene = layoutToScene(mockLayout);

  it("keeps every room and resolves names + bedroom flag", () => {
    expect(scene.rooms.map((r) => r.id).sort()).toEqual([
      "bathroom",
      "bedroom",
      "kitchen",
      "living",
    ]);
    expect(scene.rooms.find((r) => r.id === "bedroom").bedroom).toBe(true);
    expect(scene.rooms.find((r) => r.id === "kitchen").bedroom).toBe(false);
  });

  it("maps node unit-square positions into world coordinates", () => {
    const n1 = scene.nodes.find((n) => n.id === 1);
    // x=0.25 → toWorld(0.25); y(=v)=0.25 → z
    expect(n1.x).toBeCloseTo(toWorld(0.25));
    expect(n1.z).toBeCloseTo(toWorld(0.25));
    expect(toWorld(0.5)).toBe(0);
    expect(toWorld(1)).toBeCloseTo(FLOOR_SIZE / 2);
  });

  it("derives a non-empty room footprint anchored on its node", () => {
    const kitchen = scene.rooms.find((r) => r.id === "kitchen");
    expect(kitchen.width).toBeGreaterThan(0);
    expect(kitchen.depth).toBeGreaterThan(0);
    // centre near the kitchen node (unit 0.25,0.25 → negative world quadrant)
    expect(kitchen.center.x).toBeLessThan(0);
    expect(kitchen.center.z).toBeLessThan(0);
  });

  it("resolves links to node endpoints, dropping unknown ids", () => {
    expect(scene.links).toHaveLength(mockLayout.links.length);
    for (const l of scene.links) {
      expect(l.from).toBeDefined();
      expect(l.to).toBeDefined();
    }
    const broken = layoutToScene({ ...mockLayout, links: [{ a: 1, b: 999 }] });
    expect(broken.links).toHaveLength(0);
  });

  it("roomCenter falls back to origin for an unknown room", () => {
    expect(roomCenter(scene, "nope")).toMatchObject({ x: 0, z: 0 });
  });

  it("handles a layout with no geometry (synthesised fallback)", () => {
    const bare = layoutToScene({
      home_id: "h",
      rooms: [{ id: "a", name: "A" }, { id: "b", name: "B" }],
      nodes: [],
      links: [],
    });
    expect(bare.rooms).toHaveLength(2);
    // distinct fallback centres so rooms don't stack on the origin
    expect(bare.rooms[0].center).not.toEqual(bare.rooms[1].center);
  });
});
