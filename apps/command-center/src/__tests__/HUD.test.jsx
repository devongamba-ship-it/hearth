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
  // SPEC CARVE-OUT, now resolved (BAT-33). The BAT-27 live feed deliberately
  // carried no per-second vitals (privacy: aggregates only), so respiration +
  // posture rendered as "—" (decided 2026-06-11, PR #11 review concern #2).
  // BAT-33 extends the feed with coarse, smoothed 1 Hz *estimates* (still no raw
  // readings): the fields light up when present and fall back to "—" when the
  // feed omits them (confidence too low). Both paths are asserted below.
  it("renders respiration and posture as '—' when the feed omits them", () => {
    render(<HUD hud={hud} />);
    const resp = screen.getByText("Respiration").closest("div").querySelector("dd");
    const post = screen.getByText("Posture").closest("div").querySelector("dd");
    expect(resp).toHaveTextContent("—");
    expect(post).toHaveTextContent("—");
  });

  it("renders the respiration + posture estimates when the feed supplies them", () => {
    render(<HUD hud={{ ...hud, respiration: 15, posture: "upright" }} />);
    const resp = screen.getByText("Respiration").closest("div").querySelector("dd");
    const post = screen.getByText("Posture").closest("div").querySelector("dd");
    expect(resp).toHaveTextContent("15 brpm");
    expect(post).toHaveTextContent("upright");
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

  it("renders the data-source badge in the HUD strip (BAT-34)", () => {
    render(<HUD hud={hud} sourceBadge={{ kind: "live", label: "live · 2 nodes" }} />);
    const badge = screen.getByLabelText("Data source: live · 2 nodes");
    expect(badge).toHaveTextContent("live · 2 nodes");
    expect(badge).toHaveClass("source-badge", "live");
  });

  it("omits the source badge when none is supplied", () => {
    render(<HUD hud={hud} />);
    expect(screen.queryByLabelText(/Data source:/)).toBeNull();
  });
});
