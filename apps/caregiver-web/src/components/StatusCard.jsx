// Home status card — the big binary "Mom is okay" answer (BAT-25).
//
// Green / amber / red is reinforced with an icon and text, never colour alone
// (accessibility). When a fall cascade is live and unclaimed, a prominent
// "I've got it" button claims the incident.

import { useState } from "react";
import { LEVELS, composeStatus, isClaimable } from "../status.js";

const LEVEL_ICON = { green: "✓", amber: "…", red: "!" };
const LEVEL_WORD = { green: "All good", amber: "Heads up", red: "Needs attention" };

function ActivityRing({ ring }) {
  if (!ring) return null;
  const pct = Math.max(0, Math.min(100, ring.pct_of_typical ?? 0));
  return (
    <div className="ring" role="group" aria-label="Today's activity versus a typical day">
      <div
        className="ring__bar"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Today's activity is ${pct} percent of a typical day`}
      >
        <span className="ring__fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="ring__caption">
        Today {ring.active_minutes_today} active min · typical {ring.typical_active_minutes} min
        {ring.stub ? " (estimate)" : ""}
      </p>
    </div>
  );
}

export default function StatusCard({ status, activeIncidents, onClaim }) {
  const view = composeStatus(status, activeIncidents);
  const [claiming, setClaiming] = useState(false);
  const claimable = isClaimable(view);

  async function handleClaim() {
    setClaiming(true);
    try {
      await onClaim(view.incident.id);
    } finally {
      setClaiming(false);
    }
  }

  return (
    <section className={`status status--${view.level}`} aria-labelledby="status-headline">
      <p className="status__level">
        <span className="status__icon" aria-hidden="true">{LEVEL_ICON[view.level]}</span>
        <span className="visually-hidden">{LEVEL_WORD[view.level]}: </span>
      </p>
      <h2 id="status-headline" className="status__headline">{view.headline}</h2>
      <p className="status__detail">{view.detail}</p>

      {claimable && (
        <button type="button" className="btn btn--claim" onClick={handleClaim} disabled={claiming}>
          {claiming ? "Claiming…" : "I've got it"}
        </button>
      )}

      {view.level !== LEVELS.RED && <ActivityRing ring={status?.activity_ring} />}
    </section>
  );
}
