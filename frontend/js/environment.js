/* Asset loading, the world->screen transform, and the static warehouse layer.
 *
 * Follows reference/ps26123-sim-asset-loader-spec.md: source art is 256 px per grid
 * cell, anchors are normalised, and rotation happens in the renderer rather than being
 * baked into sprite variants.
 *
 * The one thing worth stating about the transform: the simulation uses +Y = north, and
 * canvas uses +Y = down. Everything is drawn through worldToScreen() so that flip lives
 * in exactly one place. Sprites are authored facing +Y (up), so a robot at heading theta
 * is drawn rotated by (PI/2 - theta) -- the negation of the maths-convention angle,
 * because the Y flip turns counter-clockwise in the world into clockwise on screen.
 */

const CELL_SRC = 256;          // authoring resolution, one grid cell
const FREE = 0, RACK = 1, STATION = 2, DOCK = 3;

/* Each asset lists CANDIDATE paths, tried in order.
 *
 * Not over-engineering: the asset set has already been reorganised twice by other people
 * on the team while this file was being written (furniture/ and cargo/ moved under
 * misc/), and a rename upstream turns into a silently blank tile down here. Listing the
 * old location as a fallback means a reorg degrades to a console warning instead of a
 * missing robot, and the renderer keeps working on either layout. */
const ASSET_PATHS = {
  tile_aisle:        ['/assets/tiles/tile_aisle_ns.png'],
  tile_intersection: ['/assets/tiles/tile_intersection.png'],
  tile_station:      ['/assets/tiles/tile_pick_station.png'],
  tile_charging:     ['/assets/tiles/tile_charging.png'],
  tile_blocked:      ['/assets/tiles/tile_blocked.png'],
  tile_fiducial:     ['/assets/tiles/tile_fiducial.png'],

  rack:              ['/assets/misc/furniture/rack_1x3.png',
                      '/assets/furniture/rack_1x3.png'],
  dock:              ['/assets/misc/furniture/charging_dock.png',
                      '/assets/furniture/charging_dock.png'],
  station:           ['/assets/misc/furniture/pick_drop_station.png',
                      '/assets/furniture/pick_drop_station.png'],
  // NOT worker_obstacle.png -- that asset is an open cardboard box despite its name.
  // This one is keyed out of the human reference card into a real sprite with alpha.
  worker:            ['/assets/misc/furniture/worker_human.png',
                      '/assets/furniture/worker_human.png'],

  robot_AMR01:       ['/assets/robots/robot_amr01_base.png'],
  robot_AMR02:       ['/assets/robots/robot_amr02_base.png'],
  robot_AMR03:       ['/assets/robots/robot_amr03_base.png'],
  robot_AMR04:       ['/assets/robots/robot_amr04_base.png'],

  halo_idle:         ['/assets/halos/halo_idle.png'],
  halo_moving:       ['/assets/halos/halo_moving.png'],
  halo_yield:        ['/assets/halos/halo_yield.png'],
  halo_deadlock:     ['/assets/halos/halo_deadlock.png'],
  halo_charging:     ['/assets/halos/halo_charging.png'],

  cargo_tote:        ['/assets/misc/cargo/cargo_tote.png',
                      '/assets/cargo/cargo_tote.png'],

  link_beam:         ['/assets/network/net_link_beam.png'],
  pulse:             ['/assets/network/broadcast_pulse.png'],
  comms_lost:        ['/assets/network/comms_lost.png'],
  deadlock:          ['/assets/network/deadlock_detected.png'],
};

/* A missing sprite must never take the whole view down with it: the dashboard is more
 * useful with one blank tile than with a blank page, and during asset iteration files
 * genuinely do come and go. Failed loads resolve to null and every draw call checks. */
function loadAssets() {
  const out = {};
  const tryPaths = (key, paths, i) => new Promise(resolve => {
    if (i >= paths.length) {
      out[key] = null;
      console.warn('asset missing, tried:', paths.join(', '));
      return resolve();
    }
    const img = new Image();
    img.onload = () => { out[key] = img; resolve(); };
    img.onerror = () => resolve(tryPaths(key, paths, i + 1));
    img.src = paths[i];
  });
  const jobs = Object.entries(ASSET_PATHS)
    .map(([key, paths]) => tryPaths(key, [].concat(paths), 0));
  return Promise.all(jobs).then(() => out);
}

/* ------------------------------------------------------------------ transform */

class View {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.cell = 32;
    this.baseCell = 32;
    this.ox = 0;
    this.oy = 0;
    this.baseOx = 0;
    this.baseOy = 0;
    this.map = null;
    this.metersPerCell = 1;
    this.cameraMode = 'overview'; // 'overview' | 'follow' | 'pov'
    this.targetId = null;
    this.zoom = 2.8;
    this.camRotation = 0;
    this.currentCam = { x: 0, y: 0, th: 0 };
  }

  /* Size the backing store to the CSS box times DPR, then fit the map inside it.
   * Skipping the DPR step is what makes a canvas dashboard look soft on a laptop. */
  resize(map, metersPerCell) {
    if (map) this.map = map;
    if (Number.isFinite(metersPerCell) && metersPerCell > 0) {
      this.metersPerCell = metersPerCell;
    }
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    const box = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.round(box.width * dpr));
    this.canvas.height = Math.max(1, Math.round(box.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cssW = box.width;
    this.cssH = box.height;
    if (!this.map) return;

    const pad = 26;
    this.baseCell = Math.max(
      8,
      Math.floor(Math.min((box.width - pad * 2) / this.map.width,
                          (box.height - pad * 2) / this.map.height))
    );
    this.baseOx = (box.width - this.map.width * this.baseCell) / 2;
    this.baseOy = (box.height - this.map.height * this.baseCell) / 2;
    this.updateCameraTransform();
  }

  setCamera(mode, targetId, zoom) {
    if (mode !== undefined) this.cameraMode = mode;
    if (targetId !== undefined) this.targetId = targetId;
    if (zoom !== undefined) this.zoom = zoom;
    this.updateCameraTransform();
  }

  updateCameraTransform(targetPos) {
    if (!this.map) return;
    if (targetPos) {
      this.currentCam.x = targetPos.x;
      this.currentCam.y = targetPos.y;
      this.currentCam.th = targetPos.th;
    }

    if (this.cameraMode === 'overview' || !this.targetId || !targetPos) {
      this.cell = this.baseCell;
      this.ox = this.baseOx;
      this.oy = this.baseOy;
      this.camRotation = 0;
      return;
    }

    // Follow or POV close-up camera
    this.cell = Math.round(this.baseCell * (this.zoom || 2.8));
    const cx = this.currentCam.x / this.metersPerCell;
    const cy = this.currentCam.y / this.metersPerCell;
    this.ox = this.cssW / 2 - cx * this.cell;
    this.oy = this.cssH / 2 - (this.map.height - cy) * this.cell;

    if (this.cameraMode === 'pov') {
      // Rotate viewport around center so robot is facing +Y (up)
      this.camRotation = this.currentCam.th - Math.PI / 2;
    } else {
      this.camRotation = 0;
    }
  }

  screenToWorld(sx, sy) {
    let px = sx, py = sy;
    if (this.camRotation !== 0) {
      const cx = this.cssW / 2, cy = this.cssH / 2;
      const cos = Math.cos(-this.camRotation), sin = Math.sin(-this.camRotation);
      const dx = px - cx, dy = py - cy;
      px = cx + (dx * cos - dy * sin);
      py = cy + (dx * sin + dy * cos);
    }
    const gridX = (px - this.ox) / this.cell;
    const gridY = this.map.height - ((py - this.oy) / this.cell);
    return [gridX * this.metersPerCell, gridY * this.metersPerCell];
  }

  /* Grid cells -> CSS pixels. Static fixtures and intent paths use cell units. */
  cellToScreen(x, y) {
    return [this.ox + x * this.cell,
            this.oy + (this.map.height - y) * this.cell];
  }

  /* World metres -> CSS pixels. The physics engine reports SI coordinates, while the
   * warehouse map is indexed in cells. Keeping the conversion here prevents a non-1 m
   * cell pitch from drawing valid robots through racks or beyond the map boundary. */
  worldToScreen(x, y) {
    return this.cellToScreen(x / this.metersPerCell,
                             y / this.metersPerCell);
  }

  cellRect(cx, cy) {
    const [sx, sy] = this.cellToScreen(cx, cy + 1);   // top-left in screen terms
    return [sx, sy, this.cell, this.cell];
  }

  clear() {
    const { ctx } = this;
    ctx.clearRect(0, 0, this.cssW, this.cssH);
  }
}

// Expose the unit-boundary primitive to the dependency-free Node regression test.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { View };
}

/* Draw a sprite centred on a world point, rotated to a heading (maths convention). */
function drawSprite(ctx, view, img, wx, wy, headingRad, sizeCells, alpha) {
  if (!img) return;
  const [sx, sy] = view.worldToScreen(wx, wy);
  const s = view.cell * (sizeCells === undefined ? 1 : sizeCells);
  ctx.save();
  if (alpha !== undefined) ctx.globalAlpha = alpha;
  ctx.translate(sx, sy);
  if (headingRad !== null && headingRad !== undefined) {
    ctx.rotate(Math.PI / 2 - headingRad);
  }
  ctx.drawImage(img, -s / 2, -s / 2, s, s);
  ctx.restore();
}

/* ------------------------------------------------------------------ map analysis */

function gridAt(map, x, y) {
  if (x < 0 || y < 0 || x >= map.width || y >= map.height) return RACK;
  return map.grid[y][x];
}
const passable = (map, x, y) => gridAt(map, x, y) !== RACK;

function degree(map, x, y) {
  let d = 0;
  if (passable(map, x + 1, y)) d++;
  if (passable(map, x - 1, y)) d++;
  if (passable(map, x, y + 1)) d++;
  if (passable(map, x, y - 1)) d++;
  return d;
}

/* Single-file blocks, recomputed client-side with the same rule the agent uses:
 * maximal connected runs of cells with at most two exits. Only long ones are drawn,
 * because only long ones are the ones the agent actually applies block control to --
 * showing the short gaps too would imply a constraint that is not being enforced. */
function findBlocks(map, minLen) {
  const key = (x, y) => y * map.width + x;
  const isCorridor = (x, y) => passable(map, x, y) && degree(map, x, y) <= 2;
  const seen = new Set();
  const blocks = [];

  for (let y = 0; y < map.height; y++) {
    for (let x = 0; x < map.width; x++) {
      if (!isCorridor(x, y) || seen.has(key(x, y))) continue;
      const stack = [[x, y]], comp = [];
      seen.add(key(x, y));
      while (stack.length) {
        const [cx, cy] = stack.pop();
        comp.push([cx, cy]);
        for (const [nx, ny] of [[cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]]) {
          if (isCorridor(nx, ny) && !seen.has(key(nx, ny))) {
            seen.add(key(nx, ny));
            stack.push([nx, ny]);
          }
        }
      }
      if (comp.length >= minLen) blocks.push(comp);
    }
  }
  return blocks;
}

/* ------------------------------------------------------------------ static layer */

/* Render floor, tiles, racks and blocks directly to any canvas context using current view transform */
function renderStaticFloor(ctx, view, map, imgs) {
  // Floor panels and navigation tiles
  for (let y = 0; y < map.height; y++) {
    for (let x = 0; x < map.width; x++) {
      const v = gridAt(map, x, y);
      if (v === RACK) continue;
      const [sx, sy, w, h] = view.cellRect(x, y);

      // Skip offscreen tiles in zoomed camera mode
      if (sx + w < -100 || sx > view.cssW + 100 || sy + h < -100 || sy > view.cssH + 100) {
        if (view.camRotation === 0) continue;
      }

      let img = null, rotate = false;
      if (v === STATION) img = imgs.tile_station;
      else if (v === DOCK) img = imgs.tile_charging;
      else if (degree(map, x, y) >= 3) img = imgs.tile_intersection;
      else {
        img = imgs.tile_aisle;
        // tile_aisle_ns runs north-south; turn it for an east-west corridor
        rotate = passable(map, x + 1, y) && passable(map, x - 1, y)
                 && !(passable(map, x, y + 1) && passable(map, x, y - 1));
      }
      ctx.save();
      if (rotate) {
        ctx.translate(sx + w / 2, sy + h / 2);
        ctx.rotate(Math.PI / 2);
        ctx.translate(-w / 2, -h / 2);
        if (img) ctx.drawImage(img, 0, 0, w, h);
      } else if (img) {
        ctx.drawImage(img, sx, sy, w, h);
      }
      ctx.restore();
    }
  }

  drawRacks(ctx, view, map, imgs);

  if (map.pedestrian_apron) {
    const [left, top] = view.cellToScreen(0, map.height);
    const [right, bottom] = view.cellToScreen(map.width, 0);
    ctx.save();
    ctx.strokeStyle = 'rgba(245,184,67,.52)';
    ctx.lineWidth = Math.max(4, view.cell * .28);
    ctx.setLineDash([Math.max(5, view.cell * .5), Math.max(4, view.cell * .3)]);
    ctx.strokeRect(left - ctx.lineWidth, top - ctx.lineWidth,
                   right - left + ctx.lineWidth * 2,
                   bottom - top + ctx.lineWidth * 2);
    ctx.restore();
  }

  // Highlight the single-file blocks the traffic layer actually controls.
  ctx.save();
  ctx.strokeStyle = 'rgba(245,184,67,.55)';
  ctx.fillStyle = 'rgba(245,184,67,.07)';
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 4]);
  for (const comp of findBlocks(map, 6)) {
    for (const [cx, cy] of comp) {
      const [sx, sy, w, h] = view.cellRect(cx, cy);
      ctx.fillRect(sx, sy, w, h);
    }
    // outline only the perimeter edges of the block
    const inBlock = new Set(comp.map(([x, y]) => y * map.width + x));
    for (const [cx, cy] of comp) {
      const [sx, sy, w, h] = view.cellRect(cx, cy);
      const has = (x, y) => inBlock.has(y * map.width + x);
      ctx.beginPath();
      if (!has(cx, cy + 1)) { ctx.moveTo(sx, sy); ctx.lineTo(sx + w, sy); }
      if (!has(cx, cy - 1)) { ctx.moveTo(sx, sy + h); ctx.lineTo(sx + w, sy + h); }
      if (!has(cx - 1, cy)) { ctx.moveTo(sx, sy); ctx.lineTo(sx + w, sy); }
      if (!has(cx + 1, cy)) { ctx.moveTo(sx + w, sy); ctx.lineTo(sx + w, sy + h); }
      ctx.stroke();
    }
  }
  ctx.restore();
}

/* The floor and furniture never change during playback, so they are rendered once to an
 * offscreen canvas and blitted per frame in overview mode. */
function buildStaticLayer(view, map, imgs) {
  const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
  const off = typeof document !== 'undefined' ? document.createElement('canvas') : { getContext: () => ({ setTransform: () => {} }) };
  off.width = Math.round(view.cssW * dpr);
  off.height = Math.round(view.cssH * dpr);
  const ctx = off.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingQuality = 'high';

  renderStaticFloor(ctx, view, map, imgs);
  return off;
}

/* rack_1x3.png is three cells wide, so it three-slices cleanly: left cap, repeating
 * middle, right cap. Stretching one sprite across a run of arbitrary length instead
 * would distort the shelving differently in every aisle. */
function drawRacks(ctx, view, map, imgs) {
  const img = imgs.rack;
  const third = CELL_SRC;
  for (let y = 0; y < map.height; y++) {
    let x = 0;
    while (x < map.width) {
      if (gridAt(map, x, y) !== RACK) { x++; continue; }
      let end = x;
      while (end + 1 < map.width && gridAt(map, end + 1, y) === RACK) end++;
      const len = end - x + 1;
      for (let i = 0; i < len; i++) {
        const [sx, sy, w, h] = view.cellRect(x + i, y);
        if (!img) {
          ctx.fillStyle = '#1b2531';
          ctx.fillRect(sx, sy, w, h);
          ctx.strokeStyle = '#2b3a4b';
          ctx.strokeRect(sx + .5, sy + .5, w - 1, h - 1);
          continue;
        }
        const slice = (len === 1) ? 1 : (i === 0 ? 0 : (i === len - 1 ? 2 : 1));
        ctx.drawImage(img, slice * third, 0, third, third, sx, sy, w, h);
      }
      x = end + 1;
    }
  }
}
