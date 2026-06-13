import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ViewBar from "../components/ViewBar.jsx";

function setup(props = {}) {
  const onView = vi.fn();
  const onStyle = vi.fn();
  render(
    <ViewBar view="birdseye" style="capsule" onView={onView} onStyle={onStyle} {...props} />,
  );
  return { onView, onStyle };
}

describe("ViewBar", () => {
  it("renders the four views and three figure styles", () => {
    setup();
    for (const v of ["Bird's eye", "Ground", "Orbit", "Field"]) {
      expect(screen.getByRole("button", { name: v })).toBeInTheDocument();
    }
    for (const s of ["Capsule", "Column", "Marker"]) {
      expect(screen.getByRole("button", { name: s })).toBeInTheDocument();
    }
  });

  it("marks the active view and style with aria-pressed", () => {
    setup({ view: "orbit", style: "marker" });
    expect(screen.getByRole("button", { name: "Orbit" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Marker" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Capsule" })).toHaveAttribute("aria-pressed", "false");
  });

  it("switching the figure style is config-only (fires the callback)", () => {
    const { onStyle, onView } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Column" }));
    expect(onStyle).toHaveBeenCalledWith("column");
    fireEvent.click(screen.getByRole("button", { name: "Ground" }));
    expect(onView).toHaveBeenCalledWith("ground");
  });

  it("shows the FALL WATCH badge only during a fall", () => {
    const { rerender } = render(
      <ViewBar view="birdseye" style="capsule" onView={() => {}} onStyle={() => {}} />,
    );
    expect(screen.queryByText(/FALL WATCH/)).not.toBeInTheDocument();
    rerender(
      <ViewBar view="birdseye" style="capsule" onView={() => {}} onStyle={() => {}} fallWatch />,
    );
    expect(screen.getByText(/FALL WATCH/)).toBeInTheDocument();
  });
});
