// Pure camera presets for the three view modes (BAT-29).
//
// Views (from the description): bird's-eye (top-down overview), ground level
// (~1.4 m camera that tracks the resident, an eye-height walk-through), and a
// slow orbit for demos. This module returns plain camera parameters — position,
// look-at target, fov — given the view, an orbit angle (advanced by the render
// loop), and an optional follow target (the resident figure's floor position).
// No Three.js here so the geometry is testable.

import { FLOOR_SIZE } from "./layout.js";

export const VIEWS = ["birdseye", "ground", "orbit"];
export const DEFAULT_VIEW = "birdseye";

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
    const back = 2.5;
    const angle = orbitAngle; // lets demos slowly arc the ground cam too
    return {
      position: [
        target.x + Math.cos(angle) * back,
        GROUND_EYE_HEIGHT,
        target.z + Math.sin(angle) * back,
      ],
      lookAt: [target.x, GROUND_EYE_HEIGHT * 0.8, target.z],
      fov: 60,
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
