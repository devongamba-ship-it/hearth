import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import Ticker from "../components/Ticker.jsx";

describe("Ticker", () => {
  it("renders an empty state", () => {
    render(<Ticker events={[]} />);
    expect(screen.getByText("No events yet")).toBeInTheDocument();
  });

  it("describes each event kind", () => {
    const events = [
      { kind: "incident", fromState: "PROBABLE", toState: "CONFIRMED_FALL", room: "bathroom", ts: 8 },
      { kind: "room_transition", trackId: "A", fromRoom: "kitchen", room: "living", ts: 3 },
      { kind: "dwell_start", trackId: "A", room: "living", ts: 7 },
      { kind: "sensing_dropout", nodeId: 2, room: "bedroom", ts: 5 },
    ];
    render(<Ticker events={events} />);
    expect(screen.getByText(/PROBABLE → CONFIRMED_FALL/)).toBeInTheDocument();
    expect(screen.getByText(/moved kitchen → living/)).toBeInTheDocument();
    expect(screen.getByText(/settled in living/)).toBeInTheDocument();
    expect(screen.getByText(/Node 2 silent in bedroom/)).toBeInTheDocument();
  });
});
