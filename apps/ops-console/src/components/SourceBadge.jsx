// Data-source badge (BAT-34): always-visible header chip telling the operator
// whether the floor is driven by simulated packets or real silicon, plus feed
// health. Colour by state: simulated (violet/neutral), live (green), degraded
// (amber), offline (red). The label/state are derived purely in source.js.

import { deriveSourceBadge } from "../source.js";

export default function SourceBadge({ connected, frame, offlineCount = 0 }) {
  const badge = deriveSourceBadge({ connected, frame, offlineCount });
  return (
    <span
      className={`source-badge ${badge.kind}`}
      role="status"
      aria-label={`Data source: ${badge.label}`}
      title="Data source — simulated packets vs live ESP32 nodes (BAT-34)"
    >
      {badge.label}
    </span>
  );
}
