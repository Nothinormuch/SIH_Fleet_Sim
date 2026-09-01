/* Robots, their status halos, carried payload, and the human worker.
 *
 * Composition over baked variants, per the asset spec: four robot bases and five halo
 * rings composite into every (robot x state) combination, rather than twenty sprites
 * that drift apart in style. The halo is drawn under the chassis so it reads as a
 * status ring on the floor rather than a sticker on the robot.
 */

const ROBOT_COLOURS = {
  AMR01: '#35c6f4',
  AMR02: '#46d39a',
  AMR03: '#f5b843',
  AMR04: '#c98bf0',
};

/* Beyond four robots the asset set repeats, and the colour is generated on a golden-
 * angle hue rotation so any fleet size stays visually distinguishable. */
function robotColour(id) {
  if (ROBOT_COLOURS[id]) return ROBOT_COLOURS[id];
  const n = parseInt(String(id).replace(/\D/g, ''), 10) || 1;
  return `hsl(${(n * 137.508) % 360}deg 72% 62%)`;
}

function robotSprite(imgs, id) {
  const direct = imgs['robot_' + id];
  if (direct) return direct;
  const n = parseInt(String(id).replace(/\D/g, ''), 10) || 1;
  const idx = ((n - 1) % 4) + 1;
  return imgs['robot_AMR0' + idx] || null;
}

const HALO_FOR = {
  idle:     'halo_idle',
  to_pick:  'halo_moving',
  to_drop:  'halo_moving',
  blocked:  'halo_deadlock',
  retreat:  'halo_yield',
  charging: 'halo_charging',
};

function drawFleet(ctx, view, frame, imgs, opts) {
  const fleetById = {};
  for (const f of (frame.fleet || [])) fleetById[f.id] = f;
  const robotSizeCells = (opts && opts.robotSizeCells) || 0.7;

  // 1. Draw stationary cargo boxes at pick cells & destination drop zones
  drawCargoField(ctx, view, frame, imgs);

  // 2. Humans first: they belong on the floor plane with the robots
  for (const h of (frame.humans || [])) {
    drawHuman(ctx, view, h, imgs);
  }

  // 3. Draw active AMR fleet
  for (const r of (frame.robots || [])) {
    const info = fleetById[r.id] || {};
    const colour = robotColour(r.id);

    const halo = imgs[HALO_FOR[info.state] || 'halo_idle'];
    drawSprite(ctx, view, halo, r.x, r.y, null, robotSizeCells * 1.35, 0.85);

    // Robot and payload rotate together as one composite
    const sprite = robotSprite(imgs, r.id);
    const [sx, sy] = view.worldToScreen(r.x, r.y);
    const size = view.cell * robotSizeCells;
    const isCarrying = r.carry || info.carry;

    ctx.save();
    ctx.translate(sx, sy);
    ctx.rotate(Math.PI / 2 - r.th);
    if (sprite) {
      ctx.drawImage(sprite, -size / 2, -size / 2, size, size);
    } else {
      ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.arc(0, 0, size * 0.34, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#04121a';
      ctx.fillRect(-size * 0.06, -size * 0.34, size * 0.12, size * 0.2);
    }

    // Render cargo box tote securely on the chassis during transport
    if (isCarrying && imgs.cargo_tote) {
      const p = size * 0.60;
      // Drop shadow for tote
      ctx.fillStyle = 'rgba(0,0,0,0.35)';
      ctx.fillRect(-p / 2 + 1, -p / 2 + 1, p, p);
      ctx.drawImage(imgs.cargo_tote, -p / 2, -p / 2, p, p);
    }
    ctx.restore();

    if (opts && opts.selectedRobotId === r.id) {
      drawTargetReticle(ctx, sx, sy, size, colour, frame.t);
    }

    if (opts && opts.labels) drawLabel(ctx, view, r, info, colour);
  }
}

/* Render cargo boxes waiting at pick stations and highlighted drop delivery zones */
function drawCargoField(ctx, view, frame, imgs) {
  const fleet = frame.fleet || [];
  const tNow = frame.t || 0;

  for (const f of fleet) {
    if (!f.task) continue;
    const colour = robotColour(f.id);

    // 1. Box waiting to be picked up at pick cell
    if (f.pick && !f.carry) {
      const [cx, cy] = f.pick;
      const [sx, sy, w, h] = view.cellRect(cx, cy);

      ctx.save();
      const pulse = 0.5 + 0.5 * Math.sin(tNow * 4);
      ctx.fillStyle = hexToRgba(colour, 0.12 + 0.08 * pulse);
      ctx.fillRect(sx + 2, sy + 2, w - 4, h - 4);
      ctx.strokeStyle = hexToRgba(colour, 0.65 + 0.3 * pulse);
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(sx + 2, sy + 2, w - 4, h - 4);

      // The cargo box sitting on the floor waiting for pickup
      if (imgs.cargo_tote) {
        const boxSize = view.cell * 0.58;
        const bx = sx + (w - boxSize) / 2;
        const by = sy + (h - boxSize) / 2;
        ctx.drawImage(imgs.cargo_tote, bx, by, boxSize, boxSize);
      }

      // Pick badge
      if (view.cell >= 18) {
        ctx.font = '700 9px ui-monospace, Menlo, Consolas, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#080c12';
        const text = `PICK ${f.task}`;
        const tw = ctx.measureText(text).width + 8;
        roundRect(ctx, sx + w / 2 - tw / 2, sy - 6, tw, 12, 3);
        ctx.fill();
        ctx.fillStyle = colour;
        ctx.fillText(text, sx + w / 2, sy);
      }
      ctx.restore();
    }

    // 2. Drop destination target at station/dock
    if (f.drop && f.carry) {
      const [gx, gy] = f.drop;
      const [gxScreen, gyScreen, gw, gh] = view.cellRect(gx, gy);

      ctx.save();
      const pulse = 0.5 + 0.5 * Math.sin(tNow * 5);
      ctx.fillStyle = hexToRgba('#46d39a', 0.14 + 0.08 * pulse);
      ctx.fillRect(gxScreen + 2, gyScreen + 2, gw - 4, gh - 4);
      ctx.strokeStyle = hexToRgba('#46d39a', 0.85);
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 4]);
      ctx.strokeRect(gxScreen + 2, gyScreen + 2, gw - 4, gh - 4);

      if (view.cell >= 18) {
        ctx.font = '700 9px ui-monospace, Menlo, Consolas, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#080c12';
        const text = `DROP ${f.task}`;
        const tw = ctx.measureText(text).width + 8;
        roundRect(ctx, gxScreen + gw / 2 - tw / 2, gyScreen - 6, tw, 12, 3);
        ctx.fill();
        ctx.fillStyle = '#46d39a';
        ctx.fillText(text, gxScreen + gw / 2, gyScreen);
      }
      ctx.restore();
    }
  }
}

function drawTargetReticle(ctx, sx, sy, size, colour, tNow) {
  ctx.save();
  const rad = size * 0.95;
  const rot = ((tNow || 0) * 1.2) % (Math.PI * 2);
  ctx.translate(sx, sy);

  // Outer segmented targeting ring
  ctx.save();
  ctx.rotate(rot);
  ctx.strokeStyle = colour;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([rad * 0.45, rad * 0.35]);
  ctx.beginPath();
  ctx.arc(0, 0, rad, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();

  // Corner brackets
  const b = rad * 1.15;
  const l = rad * 0.35;
  ctx.strokeStyle = 'rgba(255,255,255,0.85)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(-b, -b + l); ctx.lineTo(-b, -b); ctx.lineTo(-b + l, -b);
  ctx.moveTo(b - l, -b); ctx.lineTo(b, -b); ctx.lineTo(b, -b + l);
  ctx.moveTo(b, b - l); ctx.lineTo(b, b); ctx.lineTo(b - l, b);
  ctx.moveTo(-b + l, b); ctx.lineTo(-b, b); ctx.lineTo(-b, b - l);
  ctx.stroke();

  // Crosshairs
  ctx.strokeStyle = 'rgba(53,198,244,0.45)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(-rad * 1.35, 0); ctx.lineTo(-rad * 0.7, 0);
  ctx.moveTo(rad * 0.7, 0); ctx.lineTo(rad * 1.35, 0);
  ctx.moveTo(0, -rad * 1.35); ctx.lineTo(0, -rad * 0.7);
  ctx.moveTo(0, rad * 0.7); ctx.lineTo(0, rad * 1.35);
  ctx.stroke();

  ctx.restore();
}

function drawHuman(ctx, view, h, imgs) {
  const [sx, sy] = view.worldToScreen(h.x, h.y);
  const s = view.cell * 0.9;
  if (imgs.worker) {
    // Faces the way they are walking, same +Y-up sprite convention as the robots.
    ctx.save();
    ctx.translate(sx, sy);
    if (h.th !== undefined && h.th !== null) ctx.rotate(Math.PI / 2 - h.th);
    ctx.drawImage(imgs.worker, -s / 2, -s / 2, s, s);
    ctx.restore();
  } else {
    ctx.fillStyle = '#ff5f57';
    ctx.beginPath();
    ctx.arc(sx, sy, s * 0.3, 0, Math.PI * 2);
    ctx.fill();
  }
  // A dashed ring, deliberately: this agent publishes no intent, honours no priority
  // and cannot be negotiated with. Only the onboard safety layer sees it at all.
  ctx.save();
  ctx.strokeStyle = h.mode === 'working'
    ? 'rgba(70,211,154,.85)'
    : h.mode === 'yielding' ? 'rgba(245,184,67,.85)' : 'rgba(255,95,87,.75)';
  ctx.setLineDash([4, 4]);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(sx, sy, s * 0.62, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawLabel(ctx, view, r, info, colour) {
  const [sx, sy] = view.worldToScreen(r.x, r.y);
  const top = sy - view.cell * 0.72;
  const text = r.id.replace('AMR', '');
  ctx.save();
  ctx.font = '600 10px ui-monospace, Menlo, Consolas, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  const w = Math.max(16, ctx.measureText(text).width + 10);
  ctx.fillStyle = 'rgba(8,12,18,.82)';
  ctx.strokeStyle = colour;
  ctx.lineWidth = 1;
  roundRect(ctx, sx - w / 2, top - 7, w, 14, 4);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = colour;
  ctx.fillText(text, sx, top);

  // Battery pip, only when it matters. A permanent bar over every robot is noise.
  if (r.batt !== undefined && r.batt < 0.35) {
    ctx.fillStyle = r.batt < 0.15 ? '#ff5f57' : '#f5b843';
    ctx.fillRect(sx - 9, top + 10, 18 * Math.max(0.04, r.batt), 2.5);
    ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.strokeRect(sx - 9.5, top + 9.5, 19, 3.5);
  }
  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
