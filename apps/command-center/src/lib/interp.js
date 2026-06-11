// Pure client-side position interpolation (BAT-29).
//
// The live feed arrives at 1 Hz, but the scene renders at display framerate. To
// avoid figures teleporting once a second we interpolate each figure's floor
// position from where it was at the last frame toward where the newest frame
// puts it. The renderer calls `interpolateFigures` every animation tick with an
// eased alpha in [0, 1]; this module is pure (no clock, no Three.js) so the
// easing is deterministic and unit-testable.

/** Linear interpolate. */
export function lerp(a, b, t) {
  return a + (b - a) * t;
}

/** Smoothstep easing — eases in and out so 1 Hz steps don't look mechanical. */
export function smoothstep(t) {
  const c = Math.max(0, Math.min(1, t));
  return c * c * (3 - 2 * c);
}

/**
 * Interpolate `next` figures from their `prev` positions by eased `alpha`.
 *
 * A figure present in `next` but not `prev` (just appeared, e.g. a new guest)
 * snaps to its target — there is nothing to ease from. A figure that changed
 * rooms still eases, which reads as the target gliding across the floor.
 * Non-positional fields (colour, fall flags, tag) always come from `next`.
 */
export function interpolateFigures(prev, next, alpha) {
  const a = smoothstep(alpha);
  const prevById = new Map((prev ?? []).map((f) => [f.id, f]));
  return (next ?? []).map((f) => {
    const p = prevById.get(f.id);
    if (!p) return { ...f };
    return {
      ...f,
      position: {
        x: lerp(p.position.x, f.position.x, a),
        z: lerp(p.position.z, f.position.z, a),
      },
    };
  });
}
