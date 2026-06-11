// Activity screen (BAT-25): today's coarse room blocks + a day picker, plus
// weekly trend cards with plain-language deltas. Coarse blocks only — never a
// raw motion graph or per-second data (privacy is the brand).

import { useEffect, useState } from "react";
import { getActivity, getTrends } from "../api.js";

const WINDOWS = [
  { key: "24h", label: "24 hours" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
];

export default function ActivityScreen({ homeId }) {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [activity, setActivity] = useState(null);
  const [window, setWindow] = useState("7d");
  const [trends, setTrends] = useState(null);

  useEffect(() => {
    let live = true;
    getActivity(date).then((a) => live && setActivity(a)).catch(() => {});
    return () => { live = false; };
  }, [date]);

  useEffect(() => {
    let live = true;
    getTrends(homeId, window).then((t) => live && setTrends(t)).catch(() => {});
    return () => { live = false; };
  }, [homeId, window]);

  return (
    <div className="screen">
      <h2>Activity</h2>

      <label className="field">
        <span className="field__label">Day</span>
        <input
          type="date"
          value={date}
          max={new Date().toISOString().slice(0, 10)}
          onChange={(e) => setDate(e.target.value)}
        />
      </label>

      <h3>Room timeline</h3>
      {activity?.stub && (
        <p className="note">Coarse activity blocks (no per-second data, no cameras).</p>
      )}
      <ol className="timeline" aria-label="Today's activity blocks">
        {(activity?.blocks || []).map((b, i) => (
          <li key={i} className="timeline__block">
            <span className="timeline__time">{b.start}–{b.end}</span>
            <span className="timeline__room">{b.room}</span>
            <span className="timeline__label">{b.label}</span>
          </li>
        ))}
        {activity && activity.blocks.length === 0 && <li className="note">No activity recorded.</li>}
      </ol>

      <h3>Weekly trends</h3>
      <div className="segmented" role="group" aria-label="Trend window">
        {WINDOWS.map((w) => (
          <button
            key={w.key}
            type="button"
            className="segmented__btn"
            aria-pressed={window === w.key}
            onClick={() => setWindow(w.key)}
          >
            {w.label}
          </button>
        ))}
      </div>
      <div className="cards">
        {(trends?.cards || []).map((c) => (
          <div key={c.key} className="card">
            <p className="card__label">{c.label}</p>
            <p className="card__value">{c.value}</p>
            <p className="card__delta">{c.delta}</p>
          </div>
        ))}
      </div>
      {trends?.stub && <p className="note">Trend figures are representative while the wall-clock activity store lands.</p>}
    </div>
  );
}
