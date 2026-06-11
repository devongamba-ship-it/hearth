// Incident view (BAT-28 §Incident view): the operator's working surface for one
// alert. Live mesh view of the home · state-machine timeline · contact panel
// (care circle in priority order) · disposition form with reason codes, plus the
// immutable escalation cascade timeline.

import { codeChip, primaryCopy } from "../copy.js";
import { ageLabel, cascadeLabel, clockTime, duration } from "../format.js";
import ContactPanel from "./ContactPanel.jsx";
import DispositionForm from "./DispositionForm.jsx";
import MeshView from "./MeshView.jsx";
import StateTimeline from "./StateTimeline.jsx";
import Chip from "./Chip.jsx";

function CascadeTimeline({ cascade = [] }) {
  if (cascade.length === 0) return null;
  return (
    <section aria-label="Escalation timeline">
      <h3>Escalation</h3>
      <ol className="timeline">
        {cascade.map((e, i) => (
          <li key={e.at + e.kind + i} className="timeline-row">
            <time className="timeline-at">{clockTime(e.at)}</time>
            <span className="timeline-copy">{cascadeLabel(e.kind)}</span>
            {e.detail && <span className="timeline-detail">{e.detail}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function IncidentView({
  incident,
  fallDetail,
  contacts = [],
  layout,
  frame,
  ticker = [],
  trackLabel = "A",
  onClaim,
  onResolve,
}) {
  if (!incident) {
    return <p className="note">Select an alert from the queue.</p>;
  }
  const active = incident.status === "active";
  const headline = primaryCopy({ state: "CONFIRMED_FALL", trackLabel });

  return (
    <div className="incident-view">
      <header className="incident-head">
        <div className="incident-title">
          <span className="incident-home">{incident.home_name}</span>
          <span className="incident-room">{incident.room}</span>
          <Chip text={codeChip({ state: "CONFIRMED_FALL" })} title="Internal state" />
        </div>
        <p className="incident-primary">{headline}</p>
        <div className="incident-meta">
          <span>opened {clockTime(incident.created_at)}</span>
          {active && <span>age {ageLabel(incident.created_at)}</span>}
          {incident.claimed_at && (
            <span>
              response {duration(incident.created_at, incident.claimed_at)} ·{" "}
              {incident.claimed_by}
            </span>
          )}
        </div>
        {active && (
          <button type="button" className="claim-btn" onClick={() => onClaim?.(incident.id)}>
            Claim
          </button>
        )}
      </header>

      <div className="incident-grid">
        <MeshView layout={layout} frame={frame} ticker={ticker} />
        <StateTimeline detail={fallDetail} trackLabel={trackLabel} />
        <CascadeTimeline cascade={incident.cascade} />
        <ContactPanel contacts={contacts} claimedBy={incident.claimed_by} />
        <DispositionForm incident={incident} onResolve={(payload) => onResolve?.(incident.id, payload)} />
      </div>
    </div>
  );
}
