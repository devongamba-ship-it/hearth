// Live mesh view of the home (BAT-28 incident view + home detail). Renders the
// latest room_activity frame: per-room activity heat, occupancy, and anonymous
// motion tracks with identity-honest labels.
//
// Identity honesty (design v0.2): tracks show labels (A / B / guest-N), never
// names; each carries the identity-heuristic caption + confidence; two tracks in
// one room sit in deterministic offset slots with per-track colored halos
// (A teal, B violet); the room card lists "2 present · A, B".

import {
  identityCaption,
  identityLabel,
  roomPresenceCaption,
  trackColor,
  trackSlot,
} from "../copy.js";

function RoomCard({ room, tracks }) {
  const heat = Math.min(100, Math.max(0, room.activity_index || 0));
  return (
    <li className="mesh-room" style={{ "--heat": `${heat}%` }}>
      <div className="mesh-room-head">
        <span className="mesh-room-name">{room.room}</span>
        <span className="mesh-room-activity" aria-label={`activity ${heat} of 100`}>
          {heat}
        </span>
      </div>
      <div className="mesh-room-floor">
        {tracks.map((t, i) => {
          const slot = trackSlot(i);
          return (
            <span
              key={identityLabel(t) + i}
              className={`mesh-halo${t.state === "fall_watch" ? " watch" : ""}`}
              style={{
                "--halo": trackColor(identityLabel(t)),
                transform: `translate(${slot.dx}px, ${slot.dy}px)`,
              }}
              title={identityCaption(t)}
            >
              {identityLabel(t)}
            </span>
          );
        })}
      </div>
      <p className="mesh-room-presence">{roomPresenceCaption(tracks)}</p>
    </li>
  );
}

export default function MeshView({ layout, frame, ticker = [] }) {
  const rooms = frame?.rooms || [];
  const tracks = frame?.tracks || [];
  const degraded = frame?.tracking_degraded;

  // Group tracks by room so each card knows who is in it.
  const byRoom = {};
  for (const t of tracks) {
    (byRoom[t.room] ||= []).push(t);
  }

  // Order rooms by the authored layout when available, else by frame order.
  const order = (layout?.rooms || []).map((r) => r.room);
  const ordered = [...rooms].sort(
    (a, b) => (order.indexOf(a.room) + 1 || 99) - (order.indexOf(b.room) + 1 || 99),
  );

  return (
    <section className="mesh" aria-label="Live mesh view">
      <header className="mesh-head">
        <h3>Live mesh view</h3>
        {degraded && (
          <span className="badge warn" title="Track separation confidence is low">
            tracking degraded — counts only
          </span>
        )}
      </header>

      {ordered.length === 0 ? (
        <p className="note">Waiting for the live feed…</p>
      ) : (
        <ul className="mesh-grid">
          {ordered.map((room) => (
            <RoomCard key={room.room} room={room} tracks={byRoom[room.room] || []} />
          ))}
        </ul>
      )}

      {ticker.length > 0 && (
        <ul className="mesh-ticker" aria-label="Event ticker">
          {ticker.map((e, i) => (
            <li key={(e.ts || i) + (e.event || "")}>
              <span className="ticker-kind">{e.event}</span>
              <span className="ticker-room">{e.room}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="privacy-note">
        Aggregated room activity and anonymous tracks only — no camera, no audio,
        no raw sensor readings.
      </p>
    </section>
  );
}
