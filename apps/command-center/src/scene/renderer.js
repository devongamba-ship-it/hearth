// The Three.js scene for the Command Center (BAT-29).
//
// This is the one imperative, WebGL-touching module. Everything it draws is
// decided by the pure libs (layout/figures/heat/views/interp); this file just
// turns that data into meshes and runs the animation loop. It is deliberately
// NOT unit-tested (WebGL needs a real GL context); the pure libs hold the logic
// and the React layer is smoke-tested with this module mocked.
//
// Visual language (canonical design update v0.2):
//   * Floor = "sensing field": dark base, fine + major blueprint grid, glowing
//     octahedron node markers, floor-level link lines, sensing ripples pulsing
//     out of each node.
//   * Rooms = translucent tinted tiles (heat) with outline.
//   * Figures = one of three switchable styles (capsule / column / marker),
//     each with a colour, an ID tag sprite, and a halo; red + fast pulse on a
//     fall, and the capsule rotates prone to the floor plane.

import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { FLOOR_SIZE } from "../lib/layout.js";
import { cameraFor, shouldShowField } from "../lib/views.js";
import { interpolateFigures } from "../lib/interp.js";
import { FIGURE_RADIUS, FALL_COLOR } from "../lib/figures.js";

const BG = 0x05070d;

// Sensing-field particle grid: a cloud of points across the floor that rises (Y)
// and colors where the WiFi links are disturbed. 1 node = a glowing corridor;
// more nodes = the disturbance localizes where lit links cross. Driven by the
// same per-link energy as the sensing rays (animateLinks), so it needs no extra
// data — it blooms automatically as nodes come online.
const FIELD_GX = 78, FIELD_GY = 54;  // denser grid (only updated in the Field view)
const FIELD_SIGMA = 0.85;   // link band half-width, world units
const FIELD_HEIGHT = 1.5;   // max disruption extrusion along +Y
const FIELD_SHIMMER = 0.12; // gentle vertical shimmer (lower = calmer/tighter)
const FIELD_TRAIL = 0.965;  // per-point energy decay -> fading motion trail

export function createScene(container, opts = {}) {
  const intervalMs = opts.intervalMs ?? 1000;
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(container.clientWidth || 800, container.clientHeight || 600);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.Fog(BG, FLOOR_SIZE, FLOOR_SIZE * 3);

  const camera = new THREE.PerspectiveCamera(50, aspect(container), 0.1, 200);

  scene.add(new THREE.AmbientLight(0x4060a0, 0.7));
  const key = new THREE.DirectionalLight(0x9fc0ff, 0.8);
  key.position.set(FLOOR_SIZE, FLOOR_SIZE, FLOOR_SIZE);
  scene.add(key);

  buildFloor(scene);

  // Live, rebuilt-on-update groups.
  const roomGroup = new THREE.Group();
  const nodeGroup = new THREE.Group();
  const figureGroup = new THREE.Group();
  scene.add(roomGroup, nodeGroup, figureGroup);

  // Post-processing: selective bloom so emissive nodes / figures / the hot field
  // glow. The threshold keeps the dim grid + room tiles from blooming.
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(container.clientWidth || 800, container.clientHeight || 600),
    0.7, 0.5, 0.6, // strength, radius, threshold
  );
  composer.addPass(bloom);

  const ripples = [];
  const linkObjs = [];
  let latestRoomActivity = new Map();
  let field = null;
  let geometryBuilt = false;

  // Interpolation state: ease figures from prev → target across each 1 Hz tick.
  let prevFigures = [];
  let targetFigures = [];
  let lastUpdate = now();
  let view = opts.view || "birdseye";
  let style = opts.style || "capsule";
  let orbitAngle = 0;
  let latestScene = null;
  let followTarget = { x: 0, z: 0 };

  function setGeometry(sceneGeo) {
    latestScene = sceneGeo;
    rebuildRooms(roomGroup, sceneGeo);
    rebuildNodes(nodeGroup, sceneGeo, ripples, linkObjs);
    field = buildField(scene, sceneGeo);
    geometryBuilt = true;
  }

  function update(state, ui = {}) {
    if (ui.view) view = ui.view;
    if (ui.style) style = ui.style;
    if (ui.scene && !geometryBuilt) setGeometry(ui.scene);
    if (state?.rooms) {
      latestRoomActivity = new Map(state.rooms.map((r) => [r.room, r.activity_index ?? 0]));
      if (latestScene) tintRooms(roomGroup, latestScene, state.rooms);
    }
    if (state) {
      prevFigures = sampleCurrent(prevFigures, targetFigures, elapsedAlpha());
      targetFigures = state.figures || [];
      lastUpdate = now();
    }
  }

  function setView(v) { view = v; }
  function setStyle(s) { style = s; }

  function elapsedAlpha() {
    return Math.max(0, Math.min(1, (now() - lastUpdate) / intervalMs));
  }

  let raf = 0;
  function loop() {
    raf = requestAnimationFrame(loop);
    const figs = interpolateFigures(prevFigures, targetFigures, elapsedAlpha());
    drawFigures(figureGroup, figs, style);
    const resident = figs.find((f) => !f.guest) || figs[0];
    if (resident) followTarget = resident.position;
    orbitAngle += 0.0025;
    animateRipples(ripples);
    animateLinks(linkObjs, latestRoomActivity);
    if (field) {
      field.points.visible = shouldShowField(view); // gated to the Field view
      if (field.points.visible) updateField(field, linkObjs);
    }
    applyCamera(camera, view, orbitAngle, followTarget);
    composer.render();
  }
  loop();

  function resize() {
    const w = container.clientWidth || 800;
    const h = container.clientHeight || 600;
    renderer.setSize(w, h);
    composer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const onResize = () => resize();
  window.addEventListener("resize", onResize);

  function dispose() {
    cancelAnimationFrame(raf);
    window.removeEventListener("resize", onResize);
    composer.dispose?.();
    renderer.dispose();
    if (renderer.domElement.parentNode === container) {
      container.removeChild(renderer.domElement);
    }
  }

  return { update, setView, setStyle, resize, dispose, _scene: scene };
}

// -- floor / sensing field ---------------------------------------------------

function buildFloor(scene) {
  const base = new THREE.Mesh(
    new THREE.PlaneGeometry(FLOOR_SIZE * 1.4, FLOOR_SIZE * 1.4),
    new THREE.MeshBasicMaterial({ color: 0x070b14 }),
  );
  base.rotation.x = -Math.PI / 2;
  base.position.y = -0.02;
  scene.add(base);

  // Blueprint grid: a fine sub-grid and a brighter major grid.
  const fine = new THREE.GridHelper(FLOOR_SIZE * 1.4, 56, 0x16324a, 0x10233a);
  fine.position.y = 0;
  scene.add(fine);
  const major = new THREE.GridHelper(FLOOR_SIZE * 1.4, 14, 0x2f6ea0, 0x1d4060);
  major.position.y = 0.005;
  scene.add(major);
}

function rebuildNodes(group, sceneGeo, ripples, linkObjs) {
  clear(group);
  ripples.length = 0;
  linkObjs.length = 0;
  const nodeRoom = new Map(sceneGeo.nodes.map((n) => [n.id, n.room]));
  const nodeMat = new THREE.MeshStandardMaterial({
    color: 0x39d0ff,
    emissive: 0x1f6f9c,
    emissiveIntensity: 1.2,
  });
  for (const n of sceneGeo.nodes) {
    const marker = new THREE.Mesh(new THREE.OctahedronGeometry(0.16), nodeMat);
    marker.position.set(n.x, 0.16, n.z);
    group.add(marker);
    // a sensing ripple ring that expands and resets, seeded out of phase
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.2, 0.26, 32),
      new THREE.MeshBasicMaterial({
        color: 0x2f9fd0,
        transparent: true,
        opacity: 0.5,
        side: THREE.DoubleSide,
      }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(n.x, 0.01, n.z);
    ring.userData.phase = (n.id * 0.37) % 1;
    group.add(ring);
    ripples.push(ring);
  }
  // Floor-level link lines = the WiFi sensing paths between nodes that hear each
  // other. Each link gets its OWN material (animated per-link) plus a travelling
  // "energy" bead, so when a room a link touches goes active the link glows and
  // fires a bead along its length — you literally see which paths sensed motion.
  for (const l of sceneGeo.links) {
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(l.from.x, 0.04, l.from.z),
        new THREE.Vector3(l.to.x, 0.04, l.to.z),
      ]),
      new THREE.LineBasicMaterial({ color: 0x2a5f86, transparent: true, opacity: 0.22 }),
    );
    group.add(line);
    const bead = new THREE.Mesh(
      new THREE.SphereGeometry(0.09, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0x7fe6ff, transparent: true, opacity: 0 }),
    );
    bead.position.set(l.from.x, 0.08, l.from.z);
    group.add(bead);
    linkObjs.push({
      line, bead, from: l.from, to: l.to,
      roomA: nodeRoom.get(l.a), roomB: nodeRoom.get(l.b),
      energy: 0, t: (l.a * 0.31) % 1,
    });
  }
}

// WiFi sensing rays: drive each link's brightness + a travelling energy bead from
// the activity of the two rooms it connects. A quiet link sits at a faint baseline;
// a link touching an active room glows and fires a bead along its length — the
// "show me which rays picked up the activity" view.
function animateLinks(linkObjs, roomActivity) {
  for (const lk of linkObjs) {
    const a = (roomActivity.get(lk.roomA) ?? 0) / 100;
    const b = (roomActivity.get(lk.roomB) ?? 0) / 100;
    const target = Math.max(a, b); // 0..1
    lk.energy += (target - lk.energy) * 0.12; // ease toward the room activity
    const e = lk.energy;
    lk.line.material.opacity = 0.22 + 0.65 * e;
    lk.line.material.color.setRGB(0.16 + 0.55 * e, 0.37 + 0.45 * e, 0.52 + 0.45 * e);
    lk.t = (lk.t + 0.012 + 0.05 * e) % 1; // bead travels faster as energy rises
    lk.bead.position.set(
      lk.from.x + (lk.to.x - lk.from.x) * lk.t,
      0.08,
      lk.from.z + (lk.to.z - lk.from.z) * lk.t,
    );
    lk.bead.material.opacity = e > 0.04 ? 0.9 * e : 0;
    lk.bead.scale.setScalar(0.6 + 1.3 * e);
  }
}

// -- sensing field (3D particle tomography) ----------------------------------
// A point cloud across the floor. Each frame, every point's height (+Y) and
// color are recomputed from the link energies: ambient when calm, rising and
// warming where a disturbed link passes near. One node = a glowing corridor;
// with more nodes the disturbance localizes where lit links cross. Same energy
// source as the sensing rays, so it blooms automatically as nodes come online.

function buildField(scene, sceneGeo) {
  const half = FLOOR_SIZE / 2;
  const n = FIELD_GX * FIELD_GY;
  const positions = new Float32Array(n * 3);
  const colors = new Float32Array(n * 3);
  const base = new Float32Array(n * 2); // x,z per point
  const level = new Float32Array(n);    // decayed energy per point (motion trail)
  let i = 0;
  for (let gx = 0; gx < FIELD_GX; gx++) {
    for (let gy = 0; gy < FIELD_GY; gy++) {
      const x = -half + ((gx + 0.5) / FIELD_GX) * FLOOR_SIZE;
      const z = -half + ((gy + 0.5) / FIELD_GY) * FLOOR_SIZE;
      positions[i * 3] = x;
      positions[i * 3 + 1] = 0.05;
      positions[i * 3 + 2] = z;
      colors[i * 3] = 0.05; colors[i * 3 + 1] = 0.11; colors[i * 3 + 2] = 0.2;
      base[i * 2] = x; base[i * 2 + 1] = z;
      i++;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  const mat = new THREE.PointsMaterial({
    size: 0.17, vertexColors: true, transparent: true, opacity: 0.92,
    depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true,
  });
  const points = new THREE.Points(geo, mat);
  scene.add(points);
  return { points, base, positions, colors, level, t: 0 };
}

function segDist2D(px, pz, ax, az, bx, bz) {
  const vx = bx - ax, vz = bz - az, wx = px - ax, wz = pz - az;
  const t = Math.max(0, Math.min(1, (wx * vx + wz * vz) / (vx * vx + vz * vz || 1e-6)));
  return Math.hypot(px - (ax + t * vx), pz - (az + t * vz));
}

// dark blue (calm) -> teal -> amber -> red (hot), written into out[0..2].
function fieldHeat(t, out) {
  if (t < 0.04) { out[0] = 0.05; out[1] = 0.11; out[2] = 0.2; return; }
  const u = Math.min(1, (t - 0.04) / 0.96);
  if (u < 0.5) {
    const k = u / 0.5;
    out[0] = 0.13 + (0.96 - 0.13) * k;
    out[1] = 0.83 + (0.62 - 0.83) * k;
    out[2] = 0.77 + (0.04 - 0.77) * k;
  } else {
    const k = (u - 0.5) / 0.5;
    out[0] = 0.96 + (1.0 - 0.96) * k;
    out[1] = 0.62 + (0.23 - 0.62) * k;
    out[2] = 0.04 + (0.23 - 0.04) * k;
  }
}

const _fc = [0, 0, 0];
function updateField(field, linkObjs) {
  field.t += 0.04;
  const { base, positions, colors, level, points } = field;
  const sig2 = FIELD_SIGMA * FIELD_SIGMA;
  const n = base.length / 2;
  for (let i = 0; i < n; i++) {
    const x = base[i * 2], z = base[i * 2 + 1];
    let inten = 0;
    for (const lk of linkObjs) {
      if (lk.energy < 0.02) continue;
      const d = segDist2D(x, z, lk.from.x, lk.from.z, lk.to.x, lk.to.z);
      inten += lk.energy * Math.exp(-(d * d) / sig2);
    }
    // Fading motion trail: a point holds its glow and decays, so a moving
    // disturbance leaves a wake instead of snapping on and off.
    level[i] = Math.max(inten, level[i] * FIELD_TRAIL);
    let lv = 0.03 + level[i]; // + ambient floor
    if (lv > 1) lv = 1;
    const shimmer = 1 + FIELD_SHIMMER * Math.sin(field.t + i * 0.7) * lv;
    positions[i * 3 + 1] = 0.05 + lv * FIELD_HEIGHT * shimmer;
    fieldHeat(lv, _fc);
    colors[i * 3] = _fc[0]; colors[i * 3 + 1] = _fc[1]; colors[i * 3 + 2] = _fc[2];
  }
  points.geometry.attributes.position.needsUpdate = true;
  points.geometry.attributes.color.needsUpdate = true;
}

function animateRipples(ripples) {
  for (const ring of ripples) {
    ring.userData.phase = (ring.userData.phase + 0.012) % 1;
    const s = 0.4 + ring.userData.phase * 4.5;
    ring.scale.set(s, s, s);
    ring.material.opacity = 0.5 * (1 - ring.userData.phase);
  }
}

// -- rooms (heat tiles + outline) --------------------------------------------

function rebuildRooms(group, sceneGeo) {
  clear(group);
  for (const r of sceneGeo.rooms) {
    const tile = new THREE.Mesh(
      new THREE.PlaneGeometry(Math.max(0.1, r.width), Math.max(0.1, r.depth)),
      new THREE.MeshBasicMaterial({
        color: 0x14534f,
        transparent: true,
        opacity: 0.12,
        side: THREE.DoubleSide,
      }),
    );
    tile.rotation.x = -Math.PI / 2;
    tile.position.set(r.center.x, 0.012, r.center.z);
    tile.userData.roomId = r.id;
    group.add(tile);

    const outline = new THREE.LineLoop(
      roomOutlineGeometry(r),
      new THREE.LineBasicMaterial({ color: 0x3a7ca8, transparent: true, opacity: 0.5 }),
    );
    group.add(outline);
  }
}

function roomOutlineGeometry(r) {
  const { min, max } = r.footprint;
  const y = 0.02;
  return new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(min.x, y, min.z),
    new THREE.Vector3(max.x, y, min.z),
    new THREE.Vector3(max.x, y, max.z),
    new THREE.Vector3(min.x, y, max.z),
  ]);
}

function tintRooms(group, sceneGeo, roomStates) {
  const byId = new Map(roomStates.map((r) => [r.room, r.tint]));
  for (const child of group.children) {
    if (!child.userData?.roomId) continue;
    const tint = byId.get(child.userData.roomId);
    if (!tint) continue;
    child.material.color.set(tint.color);
    child.material.opacity = tint.opacity;
  }
}

// -- figures -----------------------------------------------------------------

function drawFigures(group, figures, style) {
  clear(group);
  for (const f of figures) {
    const g = buildFigure(f, style);
    g.position.set(f.position.x, 0, f.position.z);
    group.add(g);
  }
}

function buildFigure(f, style) {
  const g = new THREE.Group();
  const color = new THREE.Color(f.color);
  const mat = new THREE.MeshStandardMaterial({
    color,
    emissive: color,
    emissiveIntensity: f.fallen ? 1.4 : 0.6,
    transparent: true,
    opacity: 0.92,
  });

  let body;
  if (style === "column") {
    body = new THREE.Group();
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.18, 1.6, 16), mat);
    beam.position.y = 0.8;
    const ringBase = new THREE.Mesh(
      new THREE.TorusGeometry(0.34, 0.04, 8, 24),
      new THREE.MeshBasicMaterial({ color }),
    );
    ringBase.rotation.x = -Math.PI / 2;
    ringBase.position.y = 0.02;
    body.add(beam, ringBase);
  } else if (style === "marker") {
    body = new THREE.Group();
    const puck = new THREE.Mesh(
      new THREE.TorusGeometry(0.3, 0.06, 10, 28),
      new THREE.MeshBasicMaterial({ color }),
    );
    puck.rotation.x = -Math.PI / 2;
    puck.position.y = 0.02;
    const pip = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.5, 8), mat);
    pip.position.y = 0.27;
    body.add(puck, pip);
  } else {
    // capsule (default): a glowing body + head
    body = new THREE.Group();
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.18, 0.7, 6, 12), mat);
    torso.position.y = 0.7;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.16, 16, 16), mat);
    head.position.y = 1.3;
    body.add(torso, head);
    if (f.prone) {
      // rotate the whole body to lie on the floor plane on a confirmed fall
      body.rotation.x = -Math.PI / 2;
      body.position.set(0, 0.2, 0.4);
    }
  }
  g.add(body);

  // Halo ring under every style.
  const halo = new THREE.Mesh(
    new THREE.RingGeometry(FIGURE_RADIUS + 0.05, FIGURE_RADIUS + 0.14, 32),
    new THREE.MeshBasicMaterial({
      color: f.fallen ? new THREE.Color(FALL_COLOR) : color,
      transparent: true,
      opacity: 0.7,
      side: THREE.DoubleSide,
    }),
  );
  halo.rotation.x = -Math.PI / 2;
  halo.position.y = 0.015;
  if (f.pulse) {
    const p = 0.5 + 0.5 * Math.sin(now() / 120); // fast pulse on fall
    halo.scale.setScalar(1 + p * 0.5);
    halo.material.opacity = 0.4 + 0.6 * p;
  }
  g.add(halo);

  // ID tag sprite floating above.
  g.add(makeTagSprite(f.label, f.color, 1.8));
  return g;
}

// Tag sprite materials are cached per (label, color): drawFigures rebuilds
// figures every animation frame, and uploading a fresh CanvasTexture to the
// GPU 60 times a second per figure leaks GPU memory unbounded on a
// continuously-running wall display (PR #11 review concern #1). Cached
// materials are marked so clear() never disposes them.
const tagMaterialCache = new Map();

function tagMaterial(text, color) {
  const key = `${text}|${color}`;
  let mat = tagMaterialCache.get(key);
  if (mat) return mat;
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "rgba(5,7,13,0.7)";
  ctx.fillRect(0, 0, 128, 64);
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  ctx.strokeRect(2, 2, 124, 60);
  ctx.fillStyle = color;
  ctx.font = "bold 34px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(String(text), 64, 34);
  mat = new THREE.SpriteMaterial({
    map: new THREE.CanvasTexture(canvas),
    transparent: true,
  });
  mat.userData.cached = true;
  tagMaterialCache.set(key, mat);
  return mat;
}

function makeTagSprite(text, color, y) {
  const sprite = new THREE.Sprite(tagMaterial(text, color));
  sprite.position.set(0, y, 0);
  sprite.scale.set(0.8, 0.4, 1);
  return sprite;
}

// -- camera ------------------------------------------------------------------

function applyCamera(camera, view, orbitAngle, target) {
  const c = cameraFor(view, { orbitAngle, target });
  camera.fov = c.fov;
  camera.position.set(c.position[0], c.position[1], c.position[2]);
  camera.lookAt(c.lookAt[0], c.lookAt[1], c.lookAt[2]);
  camera.updateProjectionMatrix();
}

// -- helpers -----------------------------------------------------------------

function aspect(container) {
  return (container.clientWidth || 800) / (container.clientHeight || 600);
}

function now() {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function sampleCurrent(prev, target, alpha) {
  return interpolateFigures(prev, target, alpha);
}

function clear(group) {
  // Figures are nested Groups (body group -> meshes, halo, tag sprite), so a
  // top-level pop leaks every nested geometry/material/texture — dispose
  // recursively (PR #11 review concern #1). Cached materials (shared tag
  // sprites) are skipped; their textures live for the session by design.
  group.traverse((c) => {
    if (c === group) return;
    c.geometry?.dispose?.();
    const mats = Array.isArray(c.material) ? c.material : c.material ? [c.material] : [];
    for (const m of mats) {
      if (m.userData?.cached) continue;
      m.map?.dispose?.();
      m.dispose?.();
    }
  });
  group.clear();
}
