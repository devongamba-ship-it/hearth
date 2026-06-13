// Pure camera presets for the three view modes (BAT-29).
//
// Views (from the description): bird's-eye (top-down overview), ground level
// (~1.4 m camera that tracks the resident, an eye-height walk-through), and a
// slow orbit for demos. This module returns plain camera parameters — position,
// look-at target, fov — given the view, an orbit angle (advanced by the render
// loop), and an optional follow target (the resident figure's floor position).
// No Three.js here so the geometry is testable.

import { FLOOR_SIZE } from "./layout.js";

export const VIEWS = ["birdseye", "ground", "orbit", "field"];
export const DEFAULT_VIEW = "birdseye";

// The sensing-field particle grid is drawn ONLY in the Field view (it is dense
// and view-specific). Pure predicate so the gating decision is unit-testable
// without a WebGL context (the renderer just applies it).
export function shouldShowField(view) {
  return view === "field";
}

export const GROUND_EYE_HEIGHT = 1.4; // metres — camera height in ground view
const ORBIT_RADIUS = FLOOR_SIZE * 0.85;
const ORBIT_HEIGHT = FLOOR_SIZE * 0.55;
const BIRDSEYE_HEIGHT = FLOOR_SIZE * 1.15;

/**
 * Camera parameters for `view`.
 *
 * @param view     one of VIEWS
 * @param opts.orbitAngle  radians; the orbit view sweeps this each tick
 * @param opts.target      {x, z} floor point to look at / follow (resident)
 */
export function cameraFor(view, opts = {}) {
  const orbitAngle = opts.orbitAngle ?? 0;
  const target = opts.target ?? { x: 0, z: 0 };

  if (view === "birdseye") {
    // Straight down, whole home in frame. Nudge Z slightly so "up" on screen is
    // consistent and the floor isn't perfectly gimbal-locked.
    return {
      position: [0, BIRDSEYE_HEIGHT, 0.001],
      lookAt: [0, 0, 0],
      fov: 45,
    };
  }

  if (view === "ground") {
    // Eye height, standing a short distance from the resident and looking at
    // them — the camera tracks the follow target.
    const back = 4.5; // stand well off the resident so the figure isn't filling the lens
    const angle = orbitAngle; // slow arc around the resident
    return {
      position: [
        target.x + Math.cos(angle) * back,
        GROUND_EYE_HEIGHT + 0.4,
        target.z + Math.sin(angle) * back,
      ],
      lookAt: [target.x, GROUND_EYE_HEIGHT * 0.7, target.z],
      fov: 55,
    };
  }

  if (view === "field") {
    // Oblique low orbit, close to the floor, to read the 3D sensing-field ridge
    // (height reads best at a shallow angle, not from straight above).
    const r = FLOOR_SIZE * 0.78;
    const hgt = FLOOR_SIZE * 0.32;
    return {
      position: [Math.cos(orbitAngle) * r, hgt, Math.sin(orbitAngle) * r],
      lookAt: [0, 0.6, 0],
      fov: 55,
    };
  }

  // orbit (default fall-through): slow circle around the home centre.
  return {
    position: [
      Math.cos(orbitAngle) * ORBIT_RADIUS,
      ORBIT_HEIGHT,
      Math.sin(orbitAngle) * ORBIT_RADIUS,
    ],
    lookAt: [0, 0, 0],
    fov: 50,
  };
}
