// Pure room-heat mapping (BAT-29): translucent floor tint per activity index.
//
// The feed gives each room an `activity_index` in 0..100. We map it to a floor
// tint — a cool base for a quiet room warming toward amber as activity rises —
// plus an opacity that grows with activity so a busy room glows. Pure so the
// gradient is testable.

// Cool teal (quiet) → warm amber (busy). Interpolated in sRGB; good enough for
// a translucent floor wash.
const COOL = [0x14, 0x53, 0x4f];
const WARM = [0xf5, 0x9e, 0x0b];

function mix(a, b, t) {
  return Math.round(a + (b - a) * t);
}

/** Tint for a room from its activity index: { color: "#rrggbb", opacity }. */
export function tintFor(activityIndex) {
  const t = Math.max(0, Math.min(100, activityIndex ?? 0)) / 100;
  const r = mix(COOL[0], WARM[0], t);
  const g = mix(COOL[1], WARM[1], t);
  const b = mix(COOL[2], WARM[2], t);
  const color = "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");
  // Floor is barely visible when idle, clearly lit when busy.
  const opacity = 0.08 + 0.42 * t;
  return { color, opacity };
}
