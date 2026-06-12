import { describe, it, expect } from "vitest";
import { applyMessage, initialState, MAX_EVENTS } from "../lib/store.js";
import { layoutToScene } from "../lib/layout.js";
import { mockLayout } from "../mockFrames.js";

const scene = layoutToScene(mockLayout);
const fresh = () => initialState(scene);

const roomActivity = (over = {}) => ({
  type: "room_activity",
  home_id: "rosewood",
  ts: 1,
  rooms: [
    { room: "kitchen", activity_index: 70, occupancy_count: 1 },
    { room: "living", activity_index: 10, occupancy_count: 0 },
    { room: "bedroom", activity_index: 0, occupancy_count: 0 },
    { room: "bathroom", activity_index: 0, occupancy_count: 0 },
  ],
  tracks: [
    {
      track_id: "A",
      label: "A",
      room: "kitchen",
      confidence: 0.86,
      identity_confidence: 0.9,
      state: "normal",
    },
  ],
  tracking_degraded: false,
  ...over,
});

describe("applyMessage: room_activity", () => {
  it("builds rooms with heat tint and figures from the track array", () => {
    const s = applyMessage(fresh(), roomActivity(), scene);
    expect(s.rooms).toHaveLength(4);
    const kitchen = s.rooms.find((r) => r.room === "kitchen");
    expect(kitchen.tint.opacity).toBeGreaterThan(0.08); // warmer than idle
    expect(s.figures).toHaveLength(1);
    expect(s.figures[0].label).toBe("A");
    expect(s.tracking_degraded).toBe(false);
    expect(s.lastTs).toBe(1);
  });

  it("degrades to per-room occupancy badges with no positional figures", () => {
    const s = applyMessage(
      fresh(),
      roomActivity({
        tracks: [],
        tracking_degraded: true,
        rooms: [
          { room: "kitchen", activity_index: 20, occupancy_count: 2 },
          { room: "living", activity_index: 5, occupancy_count: 0 },
        ],
      }),
      scene,
    );
    expect(s.tracking_degraded).toBe(true);
    expect(s.figures).toHaveLength(0);
    expect(s.occupancyBadges).toEqual([{ room: "kitchen", count: 2 }]);
  });

  it("raises fallWatch when a track is in fall_watch", () => {
    const s = applyMessage(
      fresh(),
      roomActivity({
        tracks: [
          {
            track_id: "A",
            label: "A",
            room: "bathroom",
            confidence: 0.8,
            identity_confidence: 0.9,
            state: "fall_watch",
          },
        ],
      }),
      scene,
    );
    expect(s.fallWatch).toBe(true);
    expect(s.figures[0].fallen).toBe(true);
  });
});

describe("HUD model", () => {
  it("leaves respiration + posture null when the feed omits them (never fabricated)", () => {
    const s = applyMessage(fresh(), roomActivity(), scene);
    expect(s.hud.respiration).toBeNull();
    expect(s.hud.posture).toBeNull();
    // motion + confidence are derived from what the feed actually emits
    expect(s.hud.motionEnergy).toBe(70); // resident's room activity index
    expect(s.hud.confidence).toBeCloseTo(0.86);
    expect(s.hud.resident.label).toBe("A");
    expect(s.hud.resident.roomName).toBe("Kitchen");
  });

  it("surfaces the resident track's coarse vitals estimates (BAT-33) when present", () => {
    const s = applyMessage(
      fresh(),
      roomActivity({
        tracks: [
          {
            track_id: "A",
            label: "A",
            room: "kitchen",
            confidence: 0.86,
            identity_confidence: 0.9,
            state: "normal",
            respiration_bpm_estimate: 16,
            posture_estimate: "upright",
          },
        ],
      }),
      scene,
    );
    expect(s.hud.respiration).toBe(16);
    expect(s.hud.posture).toBe("upright");
  });

  it("reports no resident when none is located", () => {
    const s = applyMessage(fresh(), roomActivity({ tracks: [] }), scene);
    expect(s.hud.resident).toBeNull();
    expect(s.hud.motionEnergy).toBeNull();
  });
});

describe("applyMessage: event ticker", () => {
  it("prepends events newest-first and caps the list", () => {
    let s = fresh();
    for (let i = 0; i < MAX_EVENTS + 5; i += 1) {
      s = applyMessage(
        s,
        { type: "event", event: "room_transition", room: `r${i}`, ts: i },
        scene,
      );
    }
    expect(s.events).toHaveLength(MAX_EVENTS);
    expect(s.events[0].room).toBe(`r${MAX_EVENTS + 4}`); // newest first
  });
});

describe("applyMessage: incident", () => {
  it("sets fallWatch + incident on a watch transition and logs it", () => {
    const s = applyMessage(
      fresh(),
      {
        type: "incident",
        incident_id: "inc-1",
        node_id: 3,
        room: "bathroom",
        from_state: "PROBABLE",
        to_state: "CONFIRMED_FALL",
        kind: "confirmed_fall",
        ts: 8,
      },
      scene,
    );
    expect(s.fallWatch).toBe(true);
    expect(s.incident).toMatchObject({ incident_id: "inc-1", state: "CONFIRMED_FALL" });
    expect(s.events[0].kind).toBe("incident");
  });

  it("clears the incident when the machine returns to NORMAL", () => {
    let s = applyMessage(
      fresh(),
      { type: "incident", incident_id: "inc-1", room: "bathroom", from_state: "PROBABLE", to_state: "CONFIRMED_FALL", ts: 8 },
      scene,
    );
    s = applyMessage(
      s,
      { type: "incident", incident_id: "inc-1", room: "bathroom", from_state: "CONFIRMED_FALL", to_state: "NORMAL", ts: 11 },
      scene,
    );
    expect(s.incident).toBeNull();
    expect(s.fallWatch).toBe(false);
  });
});

describe("applyMessage: robustness", () => {
  it("passes through unknown / malformed messages unchanged", () => {
    const s = fresh();
    expect(applyMessage(s, { type: "mystery" }, scene)).toBe(s);
    expect(applyMessage(s, null, scene)).toBe(s);
  });
});
