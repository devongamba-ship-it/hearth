// Alerts screen (BAT-25): open + historical fall incidents. Selecting one
// replays both tracks of the incident — the ingest fall-machine state timeline
// (NORMAL→SUSPECTED→…→CONFIRMED_FALL) and the escalation cascade timeline (who
// was contacted, the claim, the disposition).

import { useEffect, useState } from "react";
import {
  getFallIncidents,
  getFallIncidentDetail,
  getIncidents,
  getIncidentDetail,
} from "../api.js";
import { clockTime, offset, stateLabel, cascadeLabel } from "../format.js";

function StateMachineTrack({ detail }) {
  if (!detail || !detail.transitions?.length) {
    return <p className="note">No state-machine detail for this incident.</p>;
  }
  const t0 = detail.transitions[0].at;
  return (
    <ol className="replay" aria-label="Fall detection state machine">
      {detail.transitions.map((t, i) => (
        <li key={i} className="replay__row">
          <span className="replay__time">{clockTime(t.at)} <small>{offset(t0, t.at)}</small></span>
          <span className="replay__state">{stateLabel(t.from_state)} → <strong>{stateLabel(t.to_state)}</strong></span>
          {t.reason && <span className="replay__reason">{t.reason}</span>}
        </li>
      ))}
    </ol>
  );
}

function CascadeTrack({ detail }) {
  if (!detail || !detail.cascade?.length) {
    return <p className="note">No cascade ran for this incident.</p>;
  }
  const t0 = detail.cascade[0].at;
  return (
    <ol className="replay" aria-label="Escalation cascade">
      {detail.cascade.map((e, i) => (
        <li key={i} className="replay__row">
          <span className="replay__time">{clockTime(e.at)} <small>{offset(t0, e.at)}</small></span>
          <span className="replay__state"><strong>{cascadeLabel(e.kind)}</strong></span>
          {e.detail && <span className="replay__reason">{e.detail}</span>}
        </li>
      ))}
    </ol>
  );
}

function AlertDetail({ fallIncidentId, escalationIncidentId, onBack }) {
  const [fallDetail, setFallDetail] = useState(null);
  const [cascadeDetail, setCascadeDetail] = useState(null);

  useEffect(() => {
    let live = true;
    if (fallIncidentId) {
      getFallIncidentDetail(fallIncidentId).then((d) => live && setFallDetail(d)).catch(() => {});
    }
    if (escalationIncidentId) {
      getIncidentDetail(escalationIncidentId).then((d) => live && setCascadeDetail(d)).catch(() => {});
    }
    return () => { live = false; };
  }, [fallIncidentId, escalationIncidentId]);

  return (
    <div className="screen">
      <button type="button" className="btn btn--ghost" onClick={onBack}>← Back to alerts</button>
      <h2>Fall alert</h2>
      {cascadeDetail?.disposition && (
        <p className="note">
          Resolved: {cascadeDetail.disposition.replace(/_/g, " ")}
          {cascadeDetail.disposition_reason ? ` — ${cascadeDetail.disposition_reason}` : ""}
        </p>
      )}

      <h3>How the home saw it</h3>
      <StateMachineTrack detail={fallDetail} />

      <h3>How we responded</h3>
      <CascadeTrack detail={cascadeDetail} />
    </div>
  );
}

export default function AlertsScreen() {
  const [fallIncidents, setFallIncidents] = useState([]);
  const [escIncidents, setEscIncidents] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    let live = true;
    getFallIncidents().then((r) => live && setFallIncidents(r.incidents || [])).catch(() => {});
    getIncidents().then((r) => live && setEscIncidents(r.incidents || [])).catch(() => {});
    return () => { live = false; };
  }, []);

  if (selected) {
    return (
      <AlertDetail
        fallIncidentId={selected.fallIncidentId}
        escalationIncidentId={selected.escalationIncidentId}
        onBack={() => setSelected(null)}
      />
    );
  }

  // Join the two services by the stored source fall-incident id, newest first.
  const rows = fallIncidents.map((f) => {
    const esc = escIncidents.find((e) => e.source_incident_id === f.incident_id);
    return { fall: f, esc };
  });

  return (
    <div className="screen">
      <h2>Alerts</h2>
      {rows.length === 0 && <p className="note">No fall alerts. That's the goal.</p>}
      <ul className="alerts">
        {rows.map(({ fall, esc }) => (
          <li key={fall.incident_id}>
            <button
              type="button"
              className="alerts__row"
              onClick={() =>
                setSelected({
                  fallIncidentId: fall.incident_id,
                  escalationIncidentId: esc?.id,
                })
              }
            >
              <span className="alerts__room">{fall.room}</span>
              <span className="alerts__time">{clockTime(fall.confirmed_at)}</span>
              <span className={`pill pill--${esc ? esc.status : "active"}`}>
                {esc ? esc.status : "detected"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
