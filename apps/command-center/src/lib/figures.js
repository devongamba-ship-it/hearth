// Pure track → figure mapping (BAT-29, canonical design update v0.2).
//
// The live feed (BAT-27) emits a `tracks` array of anonymous motion targets:
//   { track_id, label, room, confidence, identity_confidence, state }
// where state ∈ {"normal","fall_watch","guest"}. This module turns that array
// into render-ready *figures*: a world position (anchored on the track's room,
// offset into a deterministic non-intersecting slot when a room holds several
// tracks), a colour + ID tag, a halo, and a fall flag. It is pure: no Three.js.
//
// The renderer draws each figure in the currently selected *style*; the styles
// are switchable at runtime via the "Figures" control (capsule / column /
// marker). A figure in `fall_watch` always renders red with a fast pulse,
// regardless of style.

import { roomCenter } from "./layout.js";

// The three presence styles from the canonical update. Defaults to capsule.
export const FIGURE_STYLES = ["capsule", "column", "marker"];
export const DEFAULT_FIGURE_STYLE = "capsule";

// Per-label colours: the canonical update fixes A = teal, B = violet. Further
// known tracks cycle a stable palette; guest tracks are a muted amber so they
// read as "unrecognised / no baseline".
const LABEL_COLORS = {
  A: "#22d3c5", // teal
  B: "#a855f7", // violet
  C: "#f59e0b",
  D: "#38bdf8",
};
const GUEST_COLOR = "#9aa0a6"; // muted — anonymous, no baseline
export const FALL_COLOR = "#ff3b3b"; // red — fall_watch overrides any label colour

/** Stable colour for a track from its label (A→teal, B→violet, guest→muted). */
export function trackColor(track) {
  if (track.state === "guest" || /^guest/i.test(track.label ?? "")) {
    return GUEST_COLOR;
  }
  const key = (track.label ?? "").toUpperCase();
  if (LABEL_COLORS[key]) return LABEL_COLORS[key];
  // Deterministic fallback: hash the label into the palette so the same label
  // always lands on the same colour (no Math.random — must be reproducible).
  const palette = Object.values(LABEL_COLORS);
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return palette[h % palette.length];
}

// Slot offsets (in world units) for N co-located tracks. Slot 0 is the room
// centre; extra tracks fan out onto a fixed ring so figures never intersect.
// FIGURE_RADIUS is the on-floor footprint radius the renderer reserves.
export const FIGURE_RADIUS = 0.35;
const SLOT_RING = 0.95; // ring radius for occupants 2..N (> 2*FIGURE_RADIUS apart)

function slotOffset(index, count) {
  if (index === 0) return { dx: 0, dz: 0 };
  // Distribute occupants 1..N-1 evenly around a ring. Deterministic in `index`
  // so a given track always sits in the same slot for a given occupancy count.
  const ringCount = Math.max(1, count - 1);
  const angle = (2 * Math.PI * (index - 1)) / ringCount;
  return { dx: Math.cos(angle) * SLOT_RING, dz: Math.sin(angle) * SLOT_RING };
}

/**
 * Map the feed's `tracks` array to figures anchored on `scene` geometry.
 *
 * Tracks sharing a room are sorted deterministically by track_id and assigned
 * ring slots so they never overlap. Each figure carries colour, tag label,
 * halo, fall flag (`fallen` + `pulse`), and a `prone` flag the capsule style
 * uses to rotate the body to the floor plane on a confirmed fall.
 */
export function tracksToFigures(tracks, scene) {
  const byRoom = new Map();
  for (const t of tracks ?? []) {
    if (!byRoom.has(t.room)) byRoom.set(t.room, []);
    byRoom.get(t.room).push(t);
  }
  const figures = [];
  for (const [roomId, group] of byRoom) {
    const ordered = [...group].sort((a, b) =>
      String(a.track_id).localeCompare(String(b.track_id)),
    );
    const center = roomCenter(scene, roomId);
    ordered.forEach((t, i) => {
      const { dx, dz } = slotOffset(i, ordered.length);
      const fallen = t.state === "fall_watch";
      figures.push({
        id: String(t.track_id),
        label: t.label ?? String(t.track_id),
        room: roomId,
        position: { x: center.x + dx, z: center.z + dz },
        color: fallen ? FALL_COLOR : trackColor(t),
        baseColor: trackColor(t),
        confidence: t.confidence ?? 0,
        identityConfidence: t.identity_confidence ?? 0,
        guest: t.state === "guest",
        state: t.state ?? "normal",
        fallen,
        pulse: fallen, // fall_watch ⇒ fast pulse regardless of style
        prone: fallen, // capsule rotates to the floor plane when prone
        slot: i,
      });
    });
  }
  // Stable output order: by id, so React keys / renderer diffs are predictable.
  return figures.sort((a, b) => a.id.localeCompare(b.id));
}
