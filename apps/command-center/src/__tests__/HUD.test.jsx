import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import HUD from "../components/HUD.jsx";

const hud = {
  rooms: [
    { room: "kitchen", name: "Kitchen", activity_index: 70, occupancy_count: 1 },
    { room: "living", name: "Living room", activity_index: 10, occupancy_count: 0 },
  ],
  resident: { label: "A", room: "kitchen", roomName: "Kitchen", confidence: 0.86, identityConfidence: 0.9, state: "normal" },
  respiration: null,
  posture: null,
  motionEnergy: 70,
  confidence: 0.86,
  fallWatch: false,
};

describe("HUD", () => {
  it("shows respiration and posture as unavailable, never fabricated", () => {
    render(<HUD hud={hud} />);
    const resp = screen.getByText("Respiration").closest("div").querySelector("dd");
    const post = screen.getByText("Posture").closest("div").querySelector("dd");
    expect(resp).toHaveTextContent("—");
    expect(post).toHaveTextContent("—");
  });

  it("shows motion and confidence derived from the feed", () => {
    render(<HUD hud={hud} />);
    expect(screen.getByText("Motion").closest("div").querySelector("dd")).toHaveTextContent("70");
    expect(screen.getByText("Confidence").closest("div").querySelector("dd")).toHaveTextContent("86%");
  });

  it("lists per-room activity", () => {
    render(<HUD hud={hud} />);
    expect(screen.getByText("Kitchen")).toBeInTheDocument();
    expect(screen.getByText("Living room")).toBeInTheDocument();
  });

  it("shows the resident track and a FALL WATCH badge during a fall", () => {
    render(<HUD hud={{ ...hud, fallWatch: true, resident: { ...hud.resident, state: "fall_watch" } }} />);
    expect(screen.getByText("FALL WATCH")).toBeInTheDocument();
    expect(screen.getByText("fall_watch")).toBeInTheDocument();
  });

  it("handles a missing resident", () => {
    render(<HUD hud={{ ...hud, resident: null, motionEnergy: null, confidence: null }} />);
    expect(screen.getByText("not located")).toBeInTheDocument();
  });
});
