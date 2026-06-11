// Home detail screen (BAT-28 screen #3): sensing-health derivation and the
// read-only threshold list — the logic the a11y smoke never asserted on
// (PR #10 review concern #1).

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import HomeDetail from "../components/HomeDetail.jsx";

const layout = {
  home_id: "rosewood",
  rooms: [
    { node_id: 1, room: "Kitchen" },
    { node_id: 2, room: "Bedroom" },
  ],
};

function setup(overrides = {}) {
  const props = {
    layout,
    frame: null,
    ticker: [],
    nodes: { nodes: [{ node_id: 1, motion: 1200 }] }, // node 2 has no reading
    trends: { stub: true, cards: [] },
    thresholds: {
      stub: true,
      values: [{ key: "stillness", label: "Stillness threshold", value: "120" }],
    },
    ...overrides,
  };
  render(<HomeDetail {...props} />);
  return props;
}

describe("HomeDetail", () => {
  it("derives node online state from the presence of a latest reading", () => {
    setup();
    const rows = screen.getAllByRole("listitem")
      .filter((li) => li.className.includes("health-row"));
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("node 1");
    expect(rows[0]).toHaveTextContent("online");
    expect(rows[1]).toHaveTextContent("node 2");
    expect(rows[1]).toHaveTextContent("no reading");
  });

  it("renders thresholds read-only with the stub flag visible", () => {
    setup();
    expect(screen.getByText("Stillness threshold")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
    // The operator must never mistake this for a live control.
    expect(screen.getByText("read-only")).toBeInTheDocument();
    expect(screen.getByText(/No remote write yet/)).toBeInTheDocument();
  });
});
