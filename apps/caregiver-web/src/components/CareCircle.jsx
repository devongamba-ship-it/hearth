// Care circle (BAT-25): caregivers in cascade priority order. Reorder by drag,
// or with the keyboard up/down buttons (drag alone is not accessible). Per-person
// channels (push / SMS / call) are shown. Persistence is owned by the incident
// registry (BAT-31); this screen manages order locally for now.

import { useEffect, useState } from "react";
import { getCareCircle } from "../api.js";

const CHANNELS = ["push", "sms", "call"];
const CHANNEL_LABEL = { push: "Push", sms: "SMS", call: "Call" };

export default function CareCircle() {
  const [people, setPeople] = useState([]);
  const [dragIndex, setDragIndex] = useState(null);

  useEffect(() => {
    let live = true;
    getCareCircle().then((p) => live && setPeople(p)).catch(() => {});
    return () => { live = false; };
  }, []);

  function move(from, to) {
    if (to < 0 || to >= people.length) return;
    const next = [...people];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setPeople(next.map((p, i) => ({ ...p, priority: i + 1 })));
  }

  function toggleChannel(personId, channel) {
    setPeople((prev) =>
      prev.map((p) => {
        if (p.id !== personId) return p;
        const has = p.channels.includes(channel);
        return { ...p, channels: has ? p.channels.filter((c) => c !== channel) : [...p.channels, channel] };
      }),
    );
  }

  return (
    <div className="screen">
      <h2>Care circle</h2>
      <p className="note">Contacted in this order during a fall. Drag, or use the arrows.</p>
      <ul className="circle">
        {people.map((p, i) => (
          <li
            key={p.id}
            className="circle__item"
            draggable
            onDragStart={() => setDragIndex(i)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => { if (dragIndex !== null) move(dragIndex, i); setDragIndex(null); }}
          >
            <span className="circle__rank" aria-hidden="true">{p.priority}</span>
            <span className="circle__name">{p.name}</span>
            <span className="circle__channels">
              {CHANNELS.map((c) => (
                <button
                  key={c}
                  type="button"
                  className="chip"
                  aria-pressed={p.channels.includes(c)}
                  onClick={() => toggleChannel(p.id, c)}
                >
                  {CHANNEL_LABEL[c]}
                </button>
              ))}
            </span>
            <span className="circle__move">
              <button type="button" aria-label={`Move ${p.name} up`} disabled={i === 0} onClick={() => move(i, i - 1)}>▲</button>
              <button type="button" aria-label={`Move ${p.name} down`} disabled={i === people.length - 1} onClick={() => move(i, i + 1)}>▼</button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
