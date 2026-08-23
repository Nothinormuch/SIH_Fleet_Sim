/* The coordination layer, drawn.
 *
 * This file exists because decentralisation is invisible. A warehouse of robots moving
 * around looks identical whether they are coordinating peer-to-peer, following a central
 * schedule, or blundering past each other on luck. The messages are the whole claim, so
 * the messages get drawn: published intent, live peer links, who is blocked on whom, and
 * whether the fleet manager is reachable at all.
 *
 * Everything here is rendered from the same fields the robots actually broadcast --
 * `path` is the intent horizon in the INTENT message, `peers` is the sender's peer
 * table, `blocked_on` is the field the distributed deadlock detector runs on. Nothing is
 * synthesised for the picture.
 */

function drawNetwork(ctx, view, frame, imgs, tNow) {
  const fleet = frame.fleet || [];
  const posById = {};
  for (const r of (frame.robots || [])) posById[r.id] = r;

  drawIntents(ctx, view, fleet, posById);
  drawLinks(ctx, view, fleet, posById);
  drawGlyphs(ctx, view, fleet, posById, imgs, tNow);
}

/* The intent horizon: the cells this robot has told everyone it is about to occupy.
 * Fading along the horizon shows commitment decaying with distance, which is what the
 * time windows in the message actually encode. */
function drawIntents(ctx, view, fleet, posById) {
  ctx.save();
  for (const f of fleet) {
    const path = f.path || [];
    if (!path.length) continue;
    const colour = robotColour(f.id);
    for (let i = 0; i < path.length; i++) {
      const [cx, cy] = path[i];
      const [sx, sy, w, h] = view.cellRect(cx, cy);
      const a = 0.30 * (1 - i / (path.length + 1));
      ctx.fillStyle = hexToRgba(colour, a);
      const inset = view.cell * 0.14;
      ctx.fillRect(sx + inset, sy + inset, w - inset * 2, h - inset * 2);
    }
    // The immediate next cell is the one under contention, so outline it.
    const [nx, ny] = path[0];
    const [sx, sy, w, h] = view.cellRect(nx, ny);
    ctx.strokeStyle = hexToRgba(colour, 0.75);
    ctx.lineWidth = 1.5;
    ctx.strokeRect(sx + 2, sy + 2, w - 4, h - 4);

    // The goal, so a stalled robot's purpose is legible.
    if (f.goal) {
      const [gx, gy, gw, gh] = view.cellRect(f.goal[0], f.goal[1]);
      ctx.strokeStyle = hexToRgba(colour, 0.5);
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.strokeRect(gx + 3, gy + 3, gw - 6, gh - 6);
      ctx.setLineDash([]);
    }
  }
  ctx.restore();
}

/* One line per peer pair that can currently hear each other. Drawn once per pair, and
 * drawn UNDER the robots (this whole layer is) so a beam reads as plugging into a
 * chassis rather than cutting it in half. */
function drawLinks(ctx, view, fleet, posById) {
  const drawn = new Set();
  ctx.save();
  ctx.lineWidth = 1;
  for (const f of fleet) {
    const a = posById[f.id];
    if (!a) continue;
    for (const peerId of (f.peers || [])) {
      const key = f.id < peerId ? f.id + '|' + peerId : peerId + '|' + f.id;
      if (drawn.has(key)) continue;
      drawn.add(key);
      const b = posById[peerId];
      if (!b) continue;
      const [ax, ay] = view.worldToScreen(a.x, a.y);
      const [bx, by] = view.worldToScreen(b.x, b.y);
      const dist = Math.hypot(bx - ax, by - ay);
      // Fade with distance so a dense fleet does not become a ball of string.
      const alpha = Math.max(0.06, 0.34 - dist / (view.cell * 90));
      ctx.strokeStyle = `rgba(53,198,244,${alpha})`;
      ctx.setLineDash([3, 5]);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  ctx.restore();
}

/* Per-robot state glyphs: who is waiting for whom, and who has lost the manager. */
function drawGlyphs(ctx, view, fleet, posById, imgs, tNow) {
  ctx.save();
  for (const f of fleet) {
    const r = posById[f.id];
    if (!r) continue;
    const [sx, sy] = view.worldToScreen(r.x, r.y);

    // Waiting on a named peer -> draw the dependency. This is the wait-for graph the
    // distributed deadlock detector searches for cycles, made visible: when you can see
    // two arrows pointing at each other, you are looking at the cycle.
    if (f.blocked_on && f.blocked_on !== 'gate' && posById[f.blocked_on]) {
      const b = posById[f.blocked_on];
      const [bx, by] = view.worldToScreen(b.x, b.y);
      arrow(ctx, sx, sy, bx, by, 'rgba(255,95,87,.8)', view.cell * 0.42);
    }

    // Waiting at the mouth of a single-file block for a commit round to complete.
    if (f.blocked_on === 'gate') {
      ctx.strokeStyle = 'rgba(245,184,67,.9)';
      ctx.lineWidth = 2;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(sx, sy, view.cell * 0.62, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (f.mode === 'DEGRADED_P2P' && imgs.comms_lost) {
      const s = view.cell * 0.5;
      ctx.globalAlpha = 0.65 + 0.35 * Math.sin(tNow * 4);
      ctx.drawImage(imgs.comms_lost, sx + view.cell * 0.3, sy - view.cell * 0.75, s, s);
      ctx.globalAlpha = 1;
    }
  }
  ctx.restore();
}

/* An arrow that stops short of both endpoints, so it points *between* two robots
 * instead of being buried under their sprites. */
function arrow(ctx, x1, y1, x2, y2, colour, gap) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  if (len < gap * 2 + 4) return;
  const ux = dx / len, uy = dy / len;
  const ax = x1 + ux * gap, ay = y1 + uy * gap;
  const bx = x2 - ux * gap, by = y2 - uy * gap;

  ctx.save();
  ctx.strokeStyle = colour;
  ctx.fillStyle = colour;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(ax, ay);
  ctx.lineTo(bx, by);
  ctx.stroke();

  const head = 6;
  ctx.beginPath();
  ctx.moveTo(bx, by);
  ctx.lineTo(bx - ux * head - uy * head * 0.5, by - uy * head + ux * head * 0.5);
  ctx.lineTo(bx - ux * head + uy * head * 0.5, by - uy * head - ux * head * 0.5);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function hexToRgba(hex, a) {
  if (hex[0] !== '#') return hex.replace(')', ` / ${a})`).replace('hsl', 'hsl');
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
