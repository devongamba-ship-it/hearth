// History screen: the 14-day chart, the unified event table with type pills, and
// the export action.

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import History from "../components/History.jsx";
import { mockDailyActiveMinutes, mockIncidents } from "../mockData.js";

const NOW = 1718100000;

describe("History screen", () => {
  it("renders the event table with type pills and a resolved outcome", () => {
    render(<History incidents={mockIncidents} activeMinutes={mockDailyActiveMinutes} now={NOW} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("fall").length).toBeGreaterThan(0);
    // Resolved incident appends with its disposition + response time.
    expect(within(table).getByText(/responded 1m 0s/i)).toBeInTheDocument();
    // Person column shows the track label, not a name.
    expect(within(table).getAllByText("A").length).toBeGreaterThan(0);
  });

  it("shows a 14-day active-minutes chart", () => {
    render(<History incidents={mockIncidents} activeMinutes={mockDailyActiveMinutes} now={NOW} />);
    const chart = screen.getByRole("region", { name: /daily active minutes/i });
    expect(chart).toBeInTheDocument();
  });

  it("offers an export action for the full audit log", () => {
    render(<History incidents={mockIncidents} activeMinutes={mockDailyActiveMinutes} now={NOW} />);
    expect(screen.getByRole("button", { name: /export audit log/i })).toBeInTheDocument();
  });
});
