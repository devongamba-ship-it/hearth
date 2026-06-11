// Accessibility gate (BAT-25 acceptance: Lighthouse a11y ≥ 90).
//
// Lighthouse needs a headless browser we don't run in CI, so axe-core (the same
// engine Lighthouse uses for its accessibility audit) is the automated proxy: a
// zero-violations sweep of each screen. A human should still run Lighthouse once
// on the live app (noted in the PR).

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";

vi.mock("../api.js", async () => {
  const mock = await vi.importActual("../mockData.js");
  return {
    getStatus: () => Promise.resolve(mock.mockStatus),
    getActiveIncidents: () => Promise.resolve({ incidents: mock.mockActiveIncidents }),
    getIncidents: () => Promise.resolve({ incidents: mock.mockIncidents }),
    getIncidentDetail: () => Promise.resolve({ ...mock.mockIncidents[0], cascade: mock.mockCascade }),
    getFallIncidents: () => Promise.resolve({ incidents: mock.mockFallIncidents }),
    getFallIncidentDetail: () => Promise.resolve(mock.mockFallIncidentDetail),
    getLayout: () => Promise.resolve(mock.mockLayout),
    getTrends: () => Promise.resolve(mock.mockTrends),
    getActivity: () => Promise.resolve(mock.mockActivity),
    getCareCircle: () => Promise.resolve(mock.mockCareCircle),
    claimIncident: () => Promise.resolve({ status: "claimed" }),
  };
});

import StatusCard from "../components/StatusCard.jsx";
import ActivityScreen from "../components/ActivityScreen.jsx";
import CareCircle from "../components/CareCircle.jsx";
import Settings from "../components/Settings.jsx";
import * as mock from "../mockData.js";

async function expectClean(ui) {
  const { container } = render(ui);
  // Wait for first paint + any data effects so axe sweeps the settled tree.
  await screen.findAllByRole("heading");
  const results = await axe(container);
  expect(results).toHaveNoViolations();
}

describe("accessibility (axe)", () => {
  it("status card (green) has no violations", async () => {
    await expectClean(
      <main>
        <StatusCard status={mock.mockStatus} activeIncidents={[]} onClaim={() => {}} />
      </main>,
    );
  });

  it("status card (red cascade) has no violations", async () => {
    const incidents = [{ id: "x", room: "Kitchen", status: "active", created_at: 1 }];
    await expectClean(
      <main>
        <StatusCard status={mock.mockStatus} activeIncidents={incidents} onClaim={() => {}} />
      </main>,
    );
  });

  it("activity screen has no violations", async () => {
    await expectClean(<main><ActivityScreen homeId="home" /></main>);
  });

  it("care circle has no violations", async () => {
    await expectClean(<main><CareCircle /></main>);
  });

  it("settings has no violations", async () => {
    await expectClean(<main><Settings /></main>);
  });
});
