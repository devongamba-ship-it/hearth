// Event ticker (BAT-29): room transitions, dwell starts, sensing dropouts, and
// incident transitions, newest first. Reads the store's `events` list.

function describe(e) {
  switch (e.kind) {
    case "room_transition":
      return `Track ${e.trackId ?? "?"} moved ${e.fromRoom ?? "?"} → ${e.room}`;
    case "dwell_start":
      return `Track ${e.trackId ?? "?"} settled in ${e.room}`;
    case "sensing_dropout":
      return `Node ${e.nodeId} silent in ${e.room ?? "?"}`;
    case "incident":
      return `Incident ${e.fromState} → ${e.toState} (${e.room ?? "?"})`;
    default:
      return e.kind;
  }
}

export default function Ticker({ events }) {
  return (
    <section className="ticker" aria-label="Event ticker">
      <header>Events</header>
      <ul>
        {(events ?? []).length === 0 && <li className="ticker-empty">No events yet</li>}
        {(events ?? []).map((e, i) => (
          <li key={`${e.ts}-${i}`} className={e.kind === "incident" ? "ev-incident" : "ev"}>
            <span className="ev-ts">{e.ts ?? "—"}</span>
            <span className="ev-text">{describe(e)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
