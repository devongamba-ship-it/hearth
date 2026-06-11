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
import { FLOOR_SIZE } from "../lib/layout.js";
import { cameraFor } from "../lib/views.js";
import { interpolateFigures } from "../lib/interp.js";
import { FIGURE_RADIUS, FALL_COLOR } from "../lib/figures.js";

const BG = 0x05070d;

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

  const ripples = [];
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
    rebuildNodes(nodeGroup, sceneGeo, ripples);
    geometryBuilt = true;
  }

  function update(state, ui = {}) {
    if (ui.view) view = ui.view;
    if (ui.style) style = ui.style;
    if (ui.scene && !geometryBuilt) setGeometry(ui.scene);
    if (state?.rooms && latestScene) tintRooms(roomGroup, latestScene, state.rooms);
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
    applyCamera(camera, view, orbitAngle, followTarget);
    renderer.render(scene, camera);
  }
  loop();

  function resize() {
    const w = container.clientWidth || 800;
    const h = container.clientHeight || 600;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const onResize = () => resize();
  window.addEventListener("resize", onResize);

  function dispose() {
    cancelAnimationFrame(raf);
    window.removeEventListener("resize", onResize);
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

function rebuildNodes(group, sceneGeo, ripples) {
  clear(group);
  ripples.length = 0;
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
  // floor-level link lines between nodes that hear each other
  const linkMat = new THREE.LineBasicMaterial({ color: 0x2a5f86, transparent: true, opacity: 0.6 });
  for (const l of sceneGeo.links) {
    const geo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(l.from.x, 0.02, l.from.z),
      new THREE.Vector3(l.to.x, 0.02, l.to.z),
    ]);
    group.add(new THREE.Line(geo, linkMat));
  }
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

function makeTagSprite(text, color, y) {
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
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true }),
  );
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
  while (group.children.length) {
    const c = group.children.pop();
    c.geometry?.dispose?.();
    if (Array.isArray(c.material)) c.material.forEach((m) => m.dispose?.());
    else c.material?.dispose?.();
  }
}
