import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { mockLayout } from "../mockFrames.js";

// jsdom has no WebGL, so stub the Three.js scene wrapper. The pure store + the
// other components carry the tested logic; here we just check the app shell
// wires layout → feed → state and renders the overlays.
vi.mock("../scene/SceneView.jsx", () => ({
  default: () => <div data-testid="scene-view" />,
}));

const feedClose = vi.fn();
let feedHandler = null;
vi.mock("../api.js", () => ({
  DEFAULT_HOME: "rosewood",
  getLayout: vi.fn(async () => mockLayout),
  openLiveFeed: vi.fn((_home, onMessage) => {
    feedHandler = onMessage;
    return { close: feedClose };
  }),
}));

import App from "../App.jsx";

beforeEach(() => {
  feedHandler = null;
  feedClose.mockClear();
});

describe("App", () => {
  it("loads the layout, opens the feed, and renders the scene + HUD", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("scene-view")).toBeInTheDocument());
    expect(screen.getByLabelText("Telemetry HUD")).toBeInTheDocument();
    expect(screen.getByText("Command Center")).toBeInTheDocument();
  });

  it("folds a fall frame through to the FALL WATCH badge", async () => {
    render(<App />);
    await waitFor(() => expect(feedHandler).toBeTruthy());

    act(() =>
      feedHandler({
        type: "room_activity",
        ts: 1,
        rooms: [{ room: "bathroom", activity_index: 10, occupancy_count: 1 }],
        tracks: [
          {
            track_id: "A",
            label: "A",
            room: "bathroom",
            confidence: 0.8,
            identity_confidence: 0.9,
            state: "fall_watch",
          },
        ],
        tracking_degraded: false,
      }),
    );

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("FALL WATCH"),
    );
  });

  it("shows the degraded banner with occupancy badges", async () => {
    render(<App />);
    await waitFor(() => expect(feedHandler).toBeTruthy());

    act(() =>
      feedHandler({
        type: "room_activity",
        ts: 2,
        rooms: [{ room: "kitchen", activity_index: 20, occupancy_count: 2 }],
        tracks: [],
        tracking_degraded: true,
      }),
    );

    await waitFor(() =>
      expect(screen.getByText(/Tracking degraded/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/kitchen: 2/)).toBeInTheDocument();
  });

  it("renders an error state when the layout fails to load", async () => {
    const api = await import("../api.js");
    api.getLayout.mockRejectedValueOnce(new Error("boom"));
    render(<App />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
  });
});
