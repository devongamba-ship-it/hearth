// History screen (BAT-28 CANONICAL UPDATE #5), per home: a 14-day daily-active-
// minutes chart and a unified event table (when · type · plain-language
// description · room · person · outcome). Falls, wellness alerts, and system
// events share one timeline with type pills; resolved incidents append with
// response time. The table shows 90 days; "Export audit log" downloads the full
// history.

import { shortDate, clockTime } from "../format.js";
import { auditLogJson, buildHistory, RETENTION_DAYS } from "../history.js";

const PILL_CLASS = { fall: "pill-fall", wellness: "pill-wellness", system: "pill-system" };

function ActiveMinutesChart({ series }) {
  const days = series?.days || [];
  const max = Math.max(120, ...days.map((d) => d.active_minutes || 0));
  return (
    <section aria-label="Daily active minutes">
      <h3>
        Daily active minutes — 14 days
        {series?.stub && <span className="badge stub">representative</span>}
      </h3>
      <ul className="bars">
        {days.map((d) => (
          <li key={d.epoch} className="bar-col" title={`${shortDate(d.epoch)}: ${d.active_minutes} min`}>
            <span
              className="bar"
              style={{ height: `${Math.round(((d.active_minutes || 0) / max) * 100)}%` }}
            />
            <span className="bar-label">{shortDate(d.epoch).split(" ")[1]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function exportAudit(incidents, extraEvents) {
  const json = auditLogJson(incidents, extraEvents);
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
    return; // not available (e.g. test env) — the button is still present
  }
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "hearth-audit-log.json";
  a.click();
  URL.revokeObjectURL(url);
}

export default function History({ incidents = [], extraEvents = [], activeMinutes, now }) {
  const events = buildHistory(incidents, extraEvents, { now });
  return (
    <div className="history">
      <header className="history-head">
        <h2>History</h2>
        <button type="button" className="export-btn" onClick={() => exportAudit(incidents, extraEvents)}>
          Export audit log
        </button>
      </header>

      <ActiveMinutesChart series={activeMinutes} />

      <section aria-label="Event history">
        <h3>Events — last {RETENTION_DAYS} days</h3>
        <table className="events">
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Type</th>
              <th scope="col">What happened</th>
              <th scope="col">Room</th>
              <th scope="col">Person</th>
              <th scope="col">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr>
                <td colSpan={6} className="note">No events in this window.</td>
              </tr>
            )}
            {events.map((e, i) => (
              <tr key={(e.id || "") + (e.ts || i)}>
                <td>{shortDate(e.ts)} {clockTime(e.ts)}</td>
                <td>
                  <span className={`pill ${PILL_CLASS[e.type] || ""}`}>{e.type}</span>
                </td>
                <td>{e.description}</td>
                <td>{e.room || "—"}</td>
                <td>{e.person || "—"}</td>
                <td>{e.outcome || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
