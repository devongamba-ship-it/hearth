// DemoPanel (BAT-43): renders the scenario buttons, is loudly labelled DEMO, and
// drives the ingest demo endpoints on click. The api module is mocked so no
// backend is needed.

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../api.js", () => ({
  playDemo: vi.fn(() => Promise.resolve({ playing: true })),
  resetDemo: vi.fn(() => Promise.resolve({ reset: true })),
}));

import { playDemo, resetDemo } from "../api.js";
import DemoPanel from "../components/DemoPanel.jsx";

beforeEach(() => {
  playDemo.mockClear();
  resetDemo.mockClear();
});

describe("DemoPanel", () => {
  it("is loudly flagged DEMO and not-live", () => {
    render(<DemoPanel />);
    expect(screen.getByText("DEMO")).toBeInTheDocument();
    expect(screen.getByText(/not a live floor/i)).toBeInTheDocument();
  });

  it("renders a button per scenario plus reset", () => {
    render(<DemoPanel />);
    expect(screen.getByRole("button", { name: /fall — confirmed/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /fall — recovered/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /multi-occupancy/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /quiet morning/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reset/i })).toBeInTheDocument();
  });

  it("plays the chosen scenario and marks it active", async () => {
    render(<DemoPanel />);
    const btn = screen.getByRole("button", { name: /fall — confirmed/i });
    await userEvent.click(btn);
    expect(playDemo).toHaveBeenCalledWith("fall_confirmed");
    expect(btn).toHaveAttribute("aria-pressed", "true");
  });

  it("resets via the reset endpoint", async () => {
    render(<DemoPanel />);
    await userEvent.click(screen.getByRole("button", { name: /reset/i }));
    expect(resetDemo).toHaveBeenCalledTimes(1);
  });

  it("surfaces an error if a scenario fails to start", async () => {
    playDemo.mockRejectedValueOnce(new Error("boom"));
    render(<DemoPanel />);
    await userEvent.click(screen.getByRole("button", { name: /quiet morning/i }));
    expect(await screen.findByText(/demo error — boom/i)).toBeInTheDocument();
  });
});
