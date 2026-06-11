// Incident view: live mesh (identity-honest tracks), state-machine timeline with
// plain copy + chips, contact panel in priority order, and a disposition form
// whose resolve POSTs one of the four canonical dispositions with a reason code.

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import IncidentView from "../components/IncidentView.jsx";
import {
  mockCareCircle,
  mockFallIncidentDetail,
  mockLayout,
  mockLiveFrame,
} from "../mockData.js";

const incident = {
  id: "esc-active-1",
  home_name: "Rosewood",
  room: "Bathroom",
  status: "active",
  created_at: 1718100600,
  claimed_by: null,
  cascade: [{ at: 1718100600, kind: "incident_created", detail: "fall in Bathroom" }],
};

function setup(overrides = {}) {
  const props = {
    incident,
    fallDetail: mockFallIncidentDetail,
    contacts: mockCareCircle,
    layout: mockLayout,
    frame: mockLiveFrame,
    ticker: [],
    trackLabel: "A",
    onClaim: vi.fn(),
    onResolve: vi.fn().mockResolvedValue({}),
    ...overrides,
  };
  render(<IncidentView {...props} />);
  return props;
}

describe("IncidentView", () => {
  it("shows the mesh track by label with the identity-heuristic disclaimer, not a name", () => {
    setup();
    const mesh = screen.getByRole("region", { name: /live mesh view/i });
    // Track halo carries the honest caption "A (identity heuristic · 0.80)".
    expect(within(mesh).getByTitle(/A \(identity heuristic/)).toBeInTheDocument();
    // No resident name leaks anywhere on the surface.
    expect(screen.queryByText(/Margaret|Mom/)).not.toBeInTheDocument();
  });

  it("renders the state machine as plain copy beside code chips", () => {
    setup();
    const timeline = screen.getByRole("region", { name: /state-machine timeline/i });
    expect(within(timeline).getByText(/Possible fall/i)).toBeInTheDocument();
    expect(within(timeline).getAllByText("SUSPECTED").length).toBeGreaterThan(0);
  });

  it("lists the care circle in priority order", () => {
    setup();
    const panel = screen.getByRole("region", { name: /contact panel/i });
    const names = within(panel)
      .getAllByText(/Dana|Marcus|Pat/)
      .map((n) => n.textContent);
    expect(names[0]).toMatch(/Dana/); // priority 1
  });

  it("keeps Resolve disabled until a disposition + reason are entered, then POSTs them", async () => {
    const props = setup();
    const form = screen.getByRole("region", { name: /disposition/i });
    const resolve = within(form).getByRole("button", { name: /resolve incident/i });
    expect(resolve).toBeDisabled();

    await userEvent.click(within(form).getByLabelText(/Recovered/i));
    await userEvent.type(within(form).getByPlaceholderText(/tripped/i), "got up fine");
    expect(resolve).toBeEnabled();

    await userEvent.click(resolve);
    expect(props.onResolve).toHaveBeenCalledWith("esc-active-1", {
      disposition: "recovered",
      reason_code: "got up fine",
      by: "ops",
    });
  });

  it("offers the four canonical dispositions and no EMS-dispatch option", () => {
    setup();
    const form = screen.getByRole("region", { name: /disposition/i });
    expect(within(form).getByLabelText(/Recovered/i)).toBeInTheDocument();
    expect(within(form).getByLabelText(/Caregiver responding/i)).toBeInTheDocument();
    expect(within(form).getByLabelText(/Advised to call EMS/i)).toBeInTheDocument();
    expect(within(form).getByLabelText(/False positive/i)).toBeInTheDocument();
    expect(within(form).queryByLabelText(/dispatch/i)).not.toBeInTheDocument();
  });
});
