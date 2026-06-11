// Fall state-machine timeline (BAT-28 incident view). Renders the incident's
// NORMAL → SUSPECTED → … → CONFIRMED_FALL progression. Each row pairs the
// plain-language situation copy (§4.1) with the technical state in a code chip —
// the same primaryCopy/codeChip the rest of the console uses.

import { codeChip, primaryCopy } from "../copy.js";
import { clockTime } from "../format.js";
import Chip from "./Chip.jsx";

export default function StateTimeline({ detail, trackLabel = "A" }) {
  const transitions = detail?.transitions || [];
  if (transitions.length === 0) {
    return <p className="note">No state-machine history.</p>;
  }
  return (
    <section aria-label="State-machine timeline">
      <h3>State machine</h3>
      <ol className="timeline">
        {transitions.map((t, i) => {
          const primary = primaryCopy({ state: t.to_state, trackLabel });
          return (
            <li key={t.at + t.to_state + i} className="timeline-row">
              <time className="timeline-at">{clockTime(t.at)}</time>
              <span className="timeline-copy">{primary}</span>
              <Chip text={codeChip({ state: t.to_state })} title="Internal state" />
            </li>
          );
        })}
      </ol>
    </section>
  );
}
