// Queue screen: renders ordered rows with plain-language primary copy + a code
// chip + a live age timer, exposes a Claim action, and supports the keyboard
// shortcuts (j/k move, c claim, Enter open).

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Queue from "../components/Queue.jsx";

const incidents = [
  { id: "a", home_name: "Rosewood", room: "Bathroom", status: "active", created_at: 1000 },
  { id: "b", home_name: "Maple", room: "Living room", status: "claimed", created_at: 1500, claimed_by: "Dana" },
];

function setup(overrides = {}) {
  const props = {
    incidents,
    selectedId: "a",
    onSelect: vi.fn(),
    onClaim: vi.fn(),
    onOpen: vi.fn(),
    ...overrides,
  };
  render(<Queue {...props} />);
  return props;
}

describe("Queue", () => {
  it("renders a plain-language row with a code chip", () => {
    setup();
    expect(screen.getAllByText(/appears to be on the floor/i).length).toBeGreaterThan(0);
    // The technical state lives only in the chip, never in the primary line.
    expect(screen.getAllByText("CONFIRMED_FALL").length).toBeGreaterThan(0);
  });

  it("offers a Claim action on an unclaimed alert", async () => {
    const props = setup();
    await userEvent.click(screen.getAllByRole("button", { name: /claim/i })[0]);
    expect(props.onClaim).toHaveBeenCalledWith("a");
  });

  it("claims the selected alert with the 'c' shortcut", async () => {
    const props = setup({ selectedId: "a" });
    screen.getByRole("list", { name: /alerts/i }).focus();
    await userEvent.keyboard("c");
    expect(props.onClaim).toHaveBeenCalledWith("a");
  });

  it("opens the selected alert with Enter", async () => {
    const props = setup({ selectedId: "a" });
    screen.getByRole("list", { name: /alerts/i }).focus();
    await userEvent.keyboard("{Enter}");
    expect(props.onOpen).toHaveBeenCalledWith("a");
  });

  it("moves the selection with j", async () => {
    const props = setup({ selectedId: "a" });
    screen.getByRole("list", { name: /alerts/i }).focus();
    await userEvent.keyboard("j");
    // 'a' (active, oldest) is on top; j moves down to 'b'.
    expect(props.onSelect).toHaveBeenCalledWith("b");
  });
});
