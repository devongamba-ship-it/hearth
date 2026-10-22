// Pure layout → scene geometry (BAT-29).
//
// The ingest layout endpoint (GET /homes/{id}/layout, BAT-27) gives us rooms
// (id/name/bedroom), node markers (id/room/x/y in a 0..1 unit square), and the
// mesh link topology (node-id pairs). It does NOT carry room polygons — only
// node positions. So we derive each room's floor footprint from the positions
// of the nodes placed in it (documented assumption; see README). Everything
// here is pure data: no Three.js, no DOM — the renderer consumes the result.
//
// Coordinate convention: the authored layout is a 0..1 unit square. We map it to
// a world-space square of side FLOOR_SIZE centred on the origin, on the X/Z
// plane (Y is up — figures stand along +Y). u (0..1 horizontal) → x, v (0..1
// vertical) → z, both mapped to [-FLOOR_SIZE/2, +FLOOR_SIZE/2].

export const FLOOR_SIZE = 10; // world units across the home footprint
// Half-extent of a single-node room tile, and padding added around the bounding
// box of a multi-node room, both in unit-square fractions.
const ROOM_TILE_HALF = 0.18;
const ROOM_PAD = 0.06;

/** Map a unit-square coordinate (0..1) to a world coordinate on one axis. */
export function toWorld(unit) {
  return (unit - 0.5) * FLOOR_SIZE;
}

/** Map a unit-square (u, v) point to a world {x, z} on the floor plane. */
export function pointToWorld(u, v) {
  return { x: toWorld(u), z: toWorld(v) };
}

function clamp01(n) {
  return Math.max(0, Math.min(1, n));
}

// Derive a room's unit-square footprint rectangle from its node positions:
// the bounding box of the nodes, padded; a room with one (or zero) placed node
// gets a fixed tile centred on it (or on a fallback grid cell).
function roomFootprint(nodes, fallbackCenter) {
  const placed = nodes.filter((n) => n.x != null && n.y != null);
  if (placed.length === 0) {
    const c = fallbackCenter;
    return {
      min: { u: clamp01(c.u - ROOM_TILE_HALF), v: clamp01(c.v - ROOM_TILE_HALF) },
      max: { u: clamp01(c.u + ROOM_TILE_HALF), v: clamp01(c.v + ROOM_TILE_HALF) },
    };
  }
  let minU = 1, minV = 1, maxU = 0, maxV = 0;
  for (const n of placed) {
    minU = Math.min(minU, n.x);
    minV = Math.min(minV, n.y);
    maxU = Math.max(maxU, n.x);
    maxV = Math.max(maxV, n.y);
  }
  // A single node (or a perfectly collinear set) has zero area on an axis; grow
  // it to a tile so the room is always a visible rectangle.
  if (maxU - minU < ROOM_TILE_HALF) {
    const c = (minU + maxU) / 2;
    minU = c - ROOM_TILE_HALF;
    maxU = c + ROOM_TILE_HALF;
  }
  if (maxV - minV < ROOM_TILE_HALF) {
    const c = (minV + maxV) / 2;
    minV = c - ROOM_TILE_HALF;
    maxV = c + ROOM_TILE_HALF;
  }
  return {
    min: { u: clamp01(minU - ROOM_PAD), v: clamp01(minV - ROOM_PAD) },
    max: { u: clamp01(maxU + ROOM_PAD), v: clamp01(maxV + ROOM_PAD) },
  };
}

// Fallback grid cell centre for a room with no placed nodes: lay rooms out on
// the smallest square grid that holds them, indexed by their order.
function fallbackCenters(roomCount) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(roomCount)));
  return (i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    return { u: (col + 0.5) / cols, v: (row + 0.5) / cols };
  };
}

/**
 * Turn a layout endpoint payload into pure scene geometry.
 *
 * Returns rooms (with a world-space footprint rect + centre), nodes (world
 * positions), and links (resolved node-pair segments). The centre of each room
 * is the anchor figures stand on; the renderer draws the footprint as a tinted
 * floor tile and the nodes as glowing markers with link lines.
 */
export function layoutToScene(layout) {
  const rooms = layout?.rooms ?? [];
  const nodes = layout?.nodes ?? [];
  const links = layout?.links ?? [];
  const centerOf = fallbackCenters(rooms.length);

  const nodesByRoom = new Map();
  for (const n of nodes) {
    if (!nodesByRoom.has(n.room)) nodesByRoom.set(n.room, []);
    nodesByRoom.get(n.room).push(n);
  }

  const sceneRooms = rooms.map((r, i) => {
    // Explicit room geometry wins over node-derived squares: a `rect` {x,y,w,h}
    // in 0..1 unit-square coords lets a layout describe a REAL home (large living
    // room, small bathroom, a studio = one rectangle) instead of equal auto-tiles.
    // Falls back to the node bounding box when no rect is given.
    let fp;
    if (r.rect && r.rect.w > 0 && r.rect.h > 0) {
      fp = {
        min: { u: clamp01(r.rect.x), v: clamp01(r.rect.y) },
        max: { u: clamp01(r.rect.x + r.rect.w), v: clamp01(r.rect.y + r.rect.h) },
      };
    } else {
      const roomNodes = nodesByRoom.get(r.id) ?? [];
      fp = roomFootprint(roomNodes, centerOf(i));
    }
    const centerU = (fp.min.u + fp.max.u) / 2;
    const centerV = (fp.min.v + fp.max.v) / 2;
    return {
      id: r.id,
      name: r.name ?? r.id,
      bedroom: !!r.bedroom,
      floor: r.floor ?? 0, // level index; foundation for multi-floor homes
      footprint: {
        min: pointToWorld(fp.min.u, fp.min.v),
        max: pointToWorld(fp.max.u, fp.max.v),
      },
      width: (fp.max.u - fp.min.u) * FLOOR_SIZE,
      depth: (fp.max.v - fp.min.v) * FLOOR_SIZE,
      center: { ...pointToWorld(centerU, centerV), u: centerU, v: centerV },
    };
  });

  const nodeById = new Map();
  const sceneNodes = nodes.map((n) => {
    const u = n.x ?? 0.5;
    const v = n.y ?? 0.5;
    const world = pointToWorld(u, v);
    const sn = { id: n.id, room: n.room, floor: n.floor ?? 0, ...world };
    nodeById.set(n.id, world);
    return sn;
  });

  const sceneLinks = links
    .map((l) => {
      const a = nodeById.get(l.a);
      const b = nodeById.get(l.b);
      if (!a || !b) return null;
      return { a: l.a, b: l.b, from: a, to: b };
    })
    .filter(Boolean);

  return {
    homeId: layout?.home_id ?? "home",
    name: layout?.name ?? layout?.home_id ?? "home",
    rooms: sceneRooms,
    nodes: sceneNodes,
    links: sceneLinks,
  };
}

/** Look up a scene room by id (used to anchor figures and tint floors). */
export function roomCenter(scene, roomId) {
  const room = scene.rooms.find((r) => r.id === roomId);
  return room ? room.center : { x: 0, z: 0, u: 0.5, v: 0.5 };
}
