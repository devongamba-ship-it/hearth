// Home detail (BAT-28 §Home detail): live view + trends + sensing health (node
// uptime) + threshold config for one home.
//
// Threshold config and per-node uptime have no live backend yet: thresholds are
// read-only from detection.yaml (no write API — a follow-up), and node "online"
// status is derived from each node's latest reading age in the layout/nodes
// read. Both surfaces are flagged so an operator never mistakes them for a live
// control or a guaranteed-fresh reading.

import MeshView from "./MeshView.jsx";

function SensingHealth({ layout, nodes }) {
  const byNode = {};
  for (const n of nodes?.nodes || []) byNode[n.node_id] = n;
  const rooms = layout?.rooms || [];
  return (
    <section aria-label="Sensing health">
      <h3>Sensing health</h3>
      <ul className="health-list">
        {rooms.map((r) => {
          const reading = byNode[r.node_id];
          const online = Boolean(reading);
          return (
            <li key={r.node_id} className="health-row">
              <span className="health-node">node {r.node_id}</span>
              <span className="health-room">{r.room}</span>
              <span className={`badge ${online ? "ok" : "warn"}`}>
                {online ? "online" : "no reading"}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="note">Node uptime from the latest-reading read; live dropout shows in the mesh ticker.</p>
    </section>
  );
}

function Trends({ trends }) {
  const cards = trends?.cards || [];
  return (
    <section aria-label="Trends">
      <h3>
        Trends{trends?.stub && <span className="badge stub">representative</span>}
      </h3>
      <ul className="trend-list">
        {cards.map((c) => (
          <li key={c.key} className="trend-card">
            <span className="trend-label">{c.label}</span>
            <span className="trend-value">{c.value}</span>
            <span className="trend-delta">{c.delta}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ThresholdConfig({ thresholds }) {
  const values = thresholds?.values || [];
  return (
    <section aria-label="Threshold config">
      <h3>
        Detection thresholds
        {thresholds?.stub && <span className="badge stub">read-only</span>}
      </h3>
      <ul className="threshold-list">
        {values.map((t) => (
          <li key={t.key} className="threshold-row">
            <span className="threshold-label">{t.label}</span>
            <span className="threshold-value">{t.value}</span>
          </li>
        ))}
      </ul>
      <p className="note">
        Configured in detection.yaml on the home device. No remote write yet —
        editing is a follow-up issue.
      </p>
    </section>
  );
}

export default function HomeDetail({ layout, frame, ticker, nodes, trends, thresholds }) {
  return (
    <div className="home-detail">
      <header className="home-detail-head">
        <h2>{layout?.home_id || "Home"} — detail</h2>
      </header>
      <div className="home-detail-grid">
        <MeshView layout={layout} frame={frame} ticker={ticker} />
        <Trends trends={trends} />
        <SensingHealth layout={layout} nodes={nodes} />
        <ThresholdConfig thresholds={thresholds} />
      </div>
    </div>
  );
}
