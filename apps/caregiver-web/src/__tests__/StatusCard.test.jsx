// StatusCard renders the fall → claim transition and wires the claim action.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StatusCard from "../components/StatusCard.jsx";

const allGood = {
  state: "all_good",
  headline: "All good",
  detail: "Last activity: Kitchen, 2 min ago.",
  activity_ring: { pct_of_typical: 89, active_minutes_today: 142, typical_active_minutes: 160, stub: true },
};

describe("StatusCard", () => {
  it("shows the green all-good card with the activity ring", () => {
    render(<StatusCard status={allGood} activeIncidents={[]} onClaim={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "All good" })).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "89");
    expect(screen.queryByRole("button", { name: /got it/i })).toBeNull();
  });

  it("shows a red cascade card with a working claim button", async () => {
    const onClaim = vi.fn().mockResolvedValue();
    const incidents = [{ id: "esc-1", room: "Kitchen", status: "active", created_at: 1000 }];
    render(<StatusCard status={allGood} activeIncidents={incidents} onClaim={onClaim} />);

    expect(screen.getByText(/possible fall/i)).toBeInTheDocument();
    const section = screen.getByRole("region");
    expect(section.className).toMatch(/status--red/);

    await userEvent.click(screen.getByRole("button", { name: /got it/i }));
    expect(onClaim).toHaveBeenCalledWith("esc-1");
  });

  it("shows 'Claimed by Dana' once claimed (no claim button)", () => {
    const incidents = [
      { id: "esc-1", room: "Kitchen", status: "claimed", claimed_by: "Dana", created_at: 1000 },
    ];
    render(<StatusCard status={allGood} activeIncidents={incidents} onClaim={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Claimed by Dana" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /got it/i })).toBeNull();
  });
});
