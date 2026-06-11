// Accessibility smoke: each ops surface should have no axe violations. The
// console is dense but must stay operable (labels, roles, headings).

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "jest-axe";
import Queue from "../components/Queue.jsx";
import IncidentView from "../components/IncidentView.jsx";
import HomeDetail from "../components/HomeDetail.jsx";
import History from "../components/History.jsx";
import {
  mockCareCircle,
  mockDailyActiveMinutes,
  mockFallIncidentDetail,
  mockIncidents,
  mockLayout,
  mockLiveFrame,
  mockNodes,
  mockThresholds,
  mockTrends,
} from "../mockData.js";

const incident = { ...mockIncidents[0], cascade: [] };

describe("accessibility", () => {
  it("Queue has no violations", async () => {
    const { container } = render(
      <Queue incidents={mockIncidents} selectedId="esc-active-1" onSelect={() => {}} onClaim={() => {}} onOpen={() => {}} />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("IncidentView has no violations", async () => {
    const { container } = render(
      <IncidentView
        incident={incident}
        fallDetail={mockFallIncidentDetail}
        contacts={mockCareCircle}
        layout={mockLayout}
        frame={mockLiveFrame}
        ticker={[]}
        onClaim={() => {}}
        onResolve={async () => {}}
      />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("HomeDetail has no violations", async () => {
    const { container } = render(
      <HomeDetail
        layout={mockLayout}
        frame={mockLiveFrame}
        ticker={[]}
        nodes={mockNodes}
        trends={mockTrends}
        thresholds={mockThresholds}
      />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("History has no violations", async () => {
    const { container } = render(
      <History incidents={mockIncidents} activeMinutes={mockDailyActiveMinutes} now={1718100000} />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
