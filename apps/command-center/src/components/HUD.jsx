// HUD overlay (BAT-29): respiration, motion, posture, per-room activity,
// confidence, and the FALL WATCH badge.
//
// Honesty note (privacy brand): the aggregated live feed (BAT-27) does NOT emit
// per-second vitals — respiration and posture are not exposed by it, so we show
// them as "—" with a tooltip rather than fabricating a number. Motion is the
// activity index the feed actually provides; confidence is the resident track's.

function metric(value, unit) {
  if (value == null) return "—";
  return unit ? `${value}${unit}` : String(value);
}

export default function HUD({ hud }) {
  const resident = hud?.resident ?? null;
  const unavailable = "Not exposed by the privacy-preserving feed";
  return (
    <aside className="hud" aria-label="Telemetry HUD">
      <header className="hud-head">
        <span>Telemetry</span>
        {hud?.fallWatch && <span className="hud-fall">FALL WATCH</span>}
      </header>

      <dl className="hud-grid">
        <div>
          <dt>Respiration</dt>
          <dd title={unavailable}>{metric(hud?.respiration, " brpm")}</dd>
        </div>
        <div>
          <dt>Motion</dt>
          <dd>{metric(hud?.motionEnergy)}</dd>
        </div>
        <div>
          <dt>Posture</dt>
          <dd title={unavailable}>{hud?.posture ?? "—"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>
            {hud?.confidence == null ? "—" : `${Math.round(hud.confidence * 100)}%`}
          </dd>
        </div>
      </dl>

      <div className="hud-resident">
        <span className="hud-label">Resident</span>
        {resident ? (
          <span>
            Track {resident.label} · {resident.roomName} ·{" "}
            <span className={resident.state === "fall_watch" ? "state-fall" : "state-ok"}>
              {resident.state}
            </span>
          </span>
        ) : (
          <span>not located</span>
        )}
      </div>

      <div className="hud-rooms">
        <span className="hud-label">Per-room activity</span>
        <ul>
          {(hud?.rooms ?? []).map((r) => (
            <li key={r.room}>
              <span className="room-name">{r.name}</span>
              <span className="room-bar" aria-hidden="true">
                <span
                  className="room-bar-fill"
                  style={{ width: `${Math.max(2, r.activity_index)}%` }}
                />
              </span>
              <span className="room-val">
                {r.activity_index}
                {r.occupancy_count > 0 ? ` · ${r.occupancy_count}p` : ""}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
