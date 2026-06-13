import { describe, it, expect } from "vitest";
import {
  tracksToFigures,
  trackColor,
  FALL_COLOR,
  FIGURE_RADIUS,
  FIGURE_STYLES,
} from "../lib/figures.js";
import { layoutToScene, pointToWorld } from "../lib/layout.js";
import { mockLayout } from "../mockFrames.js";

const scene = layoutToScene(mockLayout);

const track = (over = {}) => ({
  track_id: "A",
  label: "A",
  room: "living",
  confidence: 0.8,
  identity_confidence: 0.9,
  state: "normal",
  ...over,
});

describe("trackColor", () => {
  it("fixes A→teal and B→violet, guests muted", () => {
    expect(trackColor(track({ label: "A" }))).toBe("#22d3c5");
    expect(trackColor(track({ track_id: "B", label: "B" }))).toBe("#a855f7");
    expect(trackColor(track({ label: "guest-1", state: "guest" }))).toBe("#9aa0a6");
  });

  it("is deterministic for unknown labels", () => {
    const t = track({ label: "Z" });
    expect(trackColor(t)).toBe(trackColor(t));
  });
});

describe("tracksToFigures", () => {
  it("places a track at its in-room u/v when present (multi-node localization)", () => {
    // The kitchen-walk capture carries u/v; the figure must sit at pointToWorld
    // of those, not the room centre (figures.js u/v branch).
    const here = pointToWorld(0.25, 0.25);
    const [fig] = tracksToFigures([track({ room: "kitchen", u: 0.25, v: 0.25 })], scene);
    expect(fig.position.x).toBeCloseTo(here.x);
    expect(fig.position.z).toBeCloseTo(here.z);
  });

  it("anchors a single track on its room centre", () => {
    const [fig] = tracksToFigures([track({ room: "kitchen" })], scene);
    const kitchen = scene.rooms.find((r) => r.id === "kitchen").center;
    expect(fig.position.x).toBeCloseTo(kitchen.x);
    expect(fig.position.z).toBeCloseTo(kitchen.z);
    expect(fig.slot).toBe(0);
  });

  it("gives co-located tracks distinct, non-intersecting slots", () => {
    const figs = tracksToFigures(
      [
        track({ track_id: "A", label: "A", room: "living" }),
        track({ track_id: "guest-1", label: "guest-1", room: "living", state: "guest" }),
      ],
      scene,
    );
    expect(figs).toHaveLength(2);
    const [a, b] = figs;
    const dist = Math.hypot(a.position.x - b.position.x, a.position.z - b.position.z);
    expect(dist).toBeGreaterThan(FIGURE_RADIUS * 2);
  });

  it("is deterministic: same input → same slots", () => {
    const input = [
      track({ track_id: "A", label: "A", room: "living" }),
      track({ track_id: "guest-1", label: "guest-1", room: "living", state: "guest" }),
    ];
    expect(tracksToFigures(input, scene)).toEqual(tracksToFigures(input, scene));
  });

  it("renders a fall_watch track red, pulsing, and prone", () => {
    const [fig] = tracksToFigures([track({ state: "fall_watch" })], scene);
    expect(fig.color).toBe(FALL_COLOR);
    expect(fig.fallen).toBe(true);
    expect(fig.pulse).toBe(true);
    expect(fig.prone).toBe(true);
    // base colour (the label colour) is retained for non-fall styling
    expect(fig.baseColor).toBe("#22d3c5");
  });

  it("flags guest tracks and carries identity confidence", () => {
    const [fig] = tracksToFigures(
      [track({ track_id: "guest-1", label: "guest-1", state: "guest", identity_confidence: 0 })],
      scene,
    );
    expect(fig.guest).toBe(true);
    expect(fig.identityConfidence).toBe(0);
  });

  it("exposes exactly three switchable styles", () => {
    expect(FIGURE_STYLES).toEqual(["capsule", "column", "marker"]);
  });
});
