// The selectable mock scenarios (BAT-45): the default scripted demo tour and the
// kitchen-walk replay built from real captured ESP32 data. These are the pure
// frame-source functions; the AC "kitchen-walk is selectable and replays through
// the real frame path" lives here.

import { describe, it, expect } from "vitest";
import { mockFrameStream, MOCK_SCENARIOS } from "../mockFrames.js";

const firstFrame = (msgs) => msgs.find((m) => m.type === "room_activity");

describe("mockFrameStream scenarios", () => {
  it("exposes both scenarios", () => {
    expect(MOCK_SCENARIOS).toEqual(expect.arrayContaining(["demo", "kitchen-walk"]));
  });

  it("kitchen-walk yields frames and differs from the demo tour", () => {
    const walk = mockFrameStream("kitchen-walk");
    const demo = mockFrameStream("demo");
    expect(walk.length).toBeGreaterThan(0);
    expect(walk).not.toEqual(demo); // a distinct capture, not the scripted tour
  });

  it("an unknown scenario name falls back to demo (never throws)", () => {
    expect(mockFrameStream("nope")).toEqual(mockFrameStream("demo"));
  });

  it("kitchen-walk tracks stay in the documented state enum (no fabricated posture)", () => {
    const allowed = new Set(["normal", "fall_watch", "guest"]);
    for (const tick of mockFrameStream("kitchen-walk")) {
      for (const t of firstFrame(tick)?.tracks ?? []) {
        expect(allowed.has(t.state)).toBe(true);
      }
    }
  });
});
