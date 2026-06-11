// Fall alert detail replays the state-machine + cascade timestamps (BAT-25
// acceptance). The list joins the ingest fall incident to the escalation
// incident by the stored source id; opening it shows both tracks.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../api.js", () => ({
  getFallIncidents: vi.fn(() =>
    Promise.resolve({
      incidents: [
        { incident_id: "fall-1", node_id: 1, room: "Kitchen", started_at: 1000, confirmed_at: 1048, num_transitions: 5 },
      ],
    }),
  ),
  getIncidents: vi.fn(() =>
    Promise.resolve({
      incidents: [
        { id: "esc-1", room: "Kitchen", status: "resolved", source_incident_id: "fall-1",
          created_at: 1048, disposition: "recovered", disposition_reason: "got up fine" },
      ],
    }),
  ),
  getFallIncidentDetail: vi.fn(() =>
    Promise.resolve({
      incident_id: "fall-1", node_id: 1, room: "Kitchen",
      transitions: [
        { at: 1000, reading_ts: 30000, from_state: "NORMAL", to_state: "SUSPECTED", kind: "transition", reason: null },
        { at: 1020, reading_ts: 50000, from_state: "SUSPECTED", to_state: "CHECK_IN_ACTIVE", kind: "transition", reason: null },
        { at: 1048, reading_ts: 78000, from_state: "CHECK_IN_ACTIVE", to_state: "CONFIRMED_FALL", kind: "confirmed_fall", reason: null },
      ],
    }),
  ),
  getIncidentDetail: vi.fn(() =>
    Promise.resolve({
      id: "esc-1", room: "Kitchen", status: "resolved", disposition: "recovered",
      disposition_reason: "got up fine",
      cascade: [
        { at: 1048, kind: "incident_created", priority: "P1", detail: "fall in Kitchen" },
        { at: 1048, kind: "alert_broadcast", priority: "P1", detail: "alerted 2 contacts" },
        { at: 1108, kind: "voice_calls", priority: "P1", detail: "voice ladder" },
      ],
    }),
  ),
}));

import AlertsScreen from "../components/AlertsScreen.jsx";
import * as api from "../api.js";

beforeEach(() => vi.clearAllMocks());

describe("AlertsScreen", () => {
  it("lists a fall alert and opens its dual-track replay", async () => {
    render(<AlertsScreen />);

    const row = await screen.findByRole("button", { name: /Kitchen/i });
    await userEvent.click(row);

    // Detail fetched for both services by the joined ids.
    await waitFor(() => expect(api.getFallIncidentDetail).toHaveBeenCalledWith("fall-1"));
    expect(api.getIncidentDetail).toHaveBeenCalledWith("esc-1");

    // State-machine track: the NORMAL→…→CONFIRMED_FALL replay is rendered.
    const machine = await screen.findByRole("list", { name: /state machine/i });
    expect(machine).toHaveTextContent(/Possible fall/);
    expect(machine).toHaveTextContent(/Checking in/);
    expect(machine).toHaveTextContent(/Confirmed fall/);

    // Cascade track: who/when, with timestamps.
    const cascade = screen.getByRole("list", { name: /escalation cascade/i });
    expect(cascade).toHaveTextContent(/Alerted care circle/);
    expect(cascade).toHaveTextContent(/Voice calls/);

    // Timestamps present on both tracks (offset markers).
    expect(machine).toHaveTextContent(/\+\d+s/);
    expect(cascade).toHaveTextContent(/\+1m 0s/);

    // Disposition surfaced.
    expect(screen.getByText(/recovered/i)).toBeInTheDocument();
  });

  it("shows the reassuring empty state when there are no alerts", async () => {
    api.getFallIncidents.mockResolvedValueOnce({ incidents: [] });
    api.getIncidents.mockResolvedValueOnce({ incidents: [] });
    render(<AlertsScreen />);
    expect(await screen.findByText(/No fall alerts/i)).toBeInTheDocument();
  });
});
