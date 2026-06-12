// SourceBadge renders the BAT-34 data-source state in the header. The label
// derivation is unit-tested in source.test.js; here we cover the rendered chip.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import SourceBadge from "../components/SourceBadge.jsx";

const liveFrame = {
  type: "room_activity",
  home_id: "home",
  source: "live",
  ts: 1000,
  tracking_degraded: false,
  tracks: [{ track_id: "A" }, { track_id: "guest-1" }],
};

describe("SourceBadge (BAT-34)", () => {
  it("renders the live state with the node count and colour class", () => {
    render(<SourceBadge connected frame={liveFrame} offlineCount={0} />);
    const badge = screen.getByLabelText("Data source: live · 2 nodes");
    expect(badge).toHaveTextContent("live · 2 nodes");
    expect(badge).toHaveClass("source-badge", "live");
  });

  it("renders feed offline when disconnected", () => {
    render(<SourceBadge connected={false} frame={liveFrame} />);
    const badge = screen.getByLabelText("Data source: feed offline");
    expect(badge).toHaveClass("source-badge", "offline");
  });

  it("renders the simulated state for sim-sourced frames", () => {
    render(<SourceBadge connected frame={{ ...liveFrame, source: "simulated" }} />);
    expect(screen.getByLabelText("Data source: simulated")).toHaveClass(
      "source-badge",
      "simulated",
    );
  });
});
