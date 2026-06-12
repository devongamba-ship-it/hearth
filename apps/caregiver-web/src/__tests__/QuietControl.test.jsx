// Resident "I'm OK / pause" quiet window control (BAT-39): one-tap set, live
// countdown, and cancel — and the copy that makes clear falls always alert.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import QuietControl from "../components/QuietControl.jsx";

describe("QuietControl", () => {
  it("offers a one-tap 2-hour pause and never hides that falls still alert", async () => {
    const onStart = vi.fn().mockResolvedValue();
    render(<QuietControl quiet={{ active: false, remaining_seconds: 0 }}
                         onStart={onStart} onCancel={vi.fn()} />);

    const btn = screen.getByRole("button", { name: /pause wellness alerts for 2 hours/i });
    expect(screen.getByText(/Falls always alert/i)).toBeInTheDocument();

    await userEvent.click(btn);
    expect(onStart).toHaveBeenCalledWith(2);
  });

  it("shows the remaining countdown and a cancel while active", async () => {
    const onCancel = vi.fn().mockResolvedValue();
    render(<QuietControl quiet={{ active: true, remaining_seconds: 5400 }}
                         onStart={vi.fn()} onCancel={onCancel} />);

    expect(screen.getByRole("heading", { name: /Wellness alerts paused/i })).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent("1h 30m left");
    expect(screen.getByText(/a fall will still alert/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /resume alerts/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("has no accessibility violations in either state", async () => {
    const off = render(<main><QuietControl quiet={{ active: false, remaining_seconds: 0 }}
                                            onStart={vi.fn()} onCancel={vi.fn()} /></main>);
    expect(await axe(off.container)).toHaveNoViolations();
    off.unmount();

    const on = render(<main><QuietControl quiet={{ active: true, remaining_seconds: 7200 }}
                                          onStart={vi.fn()} onCancel={vi.fn()} /></main>);
    expect(await axe(on.container)).toHaveNoViolations();
  });
});
