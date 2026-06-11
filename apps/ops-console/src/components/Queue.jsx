// Alert queue (BAT-28 §Queue). Desktop, high-density, queue-driven: alerts
// across homes, P1 falls on top with live age timers, claim/ack keyboard
// shortcuts. Each row pairs plain-language primary copy (§4.1) with a code chip.
//
// Keyboard (focus the queue, then):
//   j / ↓   next alert        k / ↑   previous alert
//   c       claim selected    Enter / o   open selected in the incident view

import { useEffect, useState } from "react";
import { codeChip, primaryCopy } from "../copy.js";
import { ageLabel } from "../format.js";
import { dispositionLabel } from "../format.js";
import { incidentPriority, queueOrder } from "../queue.js";
import Chip from "./Chip.jsx";

// Primary copy + chip for one queue row. Escalation only fires after a confirmed
// fall, so an open row's state is CONFIRMED_FALL; a resolved row shows its
// disposition instead. No confidence on the chip: live tracks carry
// identity_confidence, which is NOT fall-match confidence — conflating them
// would break the identity-honesty rule. A real match score is a backend
// follow-up.
function rowCopy(incident, track) {
  if (incident.status === "resolved") {
    return {
      primary: `Resolved — ${dispositionLabel(incident.disposition)}`,
      chip: "RESOLVED",
    };
  }
  return {
    primary: primaryCopy({ state: "CONFIRMED_FALL", trackLabel: track?.label || "A" }),
    chip: codeChip({ state: "CONFIRMED_FALL" }),
  };
}

export default function Queue({
  incidents = [],
  trackByRoom = {},
  selectedId,
  onSelect,
  onClaim,
  onOpen,
}) {
  const ordered = queueOrder(incidents);

  // A 1 Hz tick drives the live age timers without re-fetching.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const selectedIndex = ordered.findIndex((i) => i.id === selectedId);

  function move(delta) {
    if (ordered.length === 0) return;
    const next = Math.min(
      ordered.length - 1,
      Math.max(0, (selectedIndex === -1 ? 0 : selectedIndex) + delta),
    );
    onSelect?.(ordered[next].id);
  }

  function onKeyDown(e) {
    if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      move(1);
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      move(-1);
    } else if (e.key === "c") {
      if (selectedId) onClaim?.(selectedId);
    } else if (e.key === "Enter" || e.key === "o") {
      if (selectedId) onOpen?.(selectedId);
    }
  }

  return (
    <section className="queue" aria-label="Alert queue">
      <header className="queue-head">
        <h2>Alert queue</h2>
        <p className="queue-hint">
          <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>c</kbd> claim · <kbd>Enter</kbd> open
        </p>
      </header>
      {ordered.length === 0 ? (
        <p className="note">No active alerts. That's the goal.</p>
      ) : (
        <ul
          className="queue-list"
          aria-label="Alerts"
          tabIndex={0}
          onKeyDown={onKeyDown}
        >
          {ordered.map((incident) => {
            const track = trackByRoom[incident.room];
            const { primary, chip } = rowCopy(incident, track);
            const selected = incident.id === selectedId;
            return (
              <li
                key={incident.id}
                aria-current={selected ? "true" : undefined}
                className={`queue-row ${incident.status}${selected ? " selected" : ""}`}
                onClick={() => onSelect?.(incident.id)}
                onDoubleClick={() => onOpen?.(incident.id)}
              >
                <span className={`prio ${incidentPriority(incident).toLowerCase()}`}>
                  {incidentPriority(incident)}
                </span>
                <span className="queue-home">{incident.home_name}</span>
                <span className="queue-room">{incident.room}</span>
                <span className="queue-primary">{primary}</span>
                <Chip text={chip} title="Internal state" />
                <time className="queue-age" aria-label="age">
                  {incident.status === "resolved" ? "—" : ageLabel(incident.created_at)}
                </time>
                <span className="queue-status">
                  {incident.status === "claimed"
                    ? `claimed · ${incident.claimed_by}`
                    : incident.status}
                </span>
                {incident.status === "active" && (
                  <button
                    type="button"
                    className="queue-claim"
                    onClick={(e) => {
                      e.stopPropagation();
                      onClaim?.(incident.id);
                    }}
                  >
                    Claim
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
