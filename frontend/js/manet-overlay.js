/* MANET Overlay — 2D canvas visualization of the wireless topology.
 *
 * Follows the same pattern as network.js: plain functions that take
 * (ctx, view, frame, ...) and draw on the 2D diagnostic canvas.
 * Called from draw() in main.js alongside drawNetwork().
 */

function drawManetOverlay(ctx, view, frame, imgs, tNow) {
  if (!ManetSim.isReady()) return;
  const fleet = frame.fleet || [];
  const robots = frame.robots || [];
  const posById = {};
  for (const r of robots) posById[r.id] = r;

  drawCoverageRings(ctx, view, robots, tNow);
  drawRSSILinks(ctx, view, fleet, posById, tNow);
  drawPacketTrails(ctx, view, posById, tNow);
  drawDeadZoneHatch(ctx, view, tNow);
  drawNodeLabels(ctx, view, robots);
}

/* ── Coverage Circles ────────────────────────────────────────────────── */

function drawCoverageRings(ctx, view, robots, tNow) {
  ctx.save();
  const coverageM = 12; // approximate indoor Wi-Fi range in metres
  for (const r of robots) {
    const [sx, sy] = view.worldToScreen(r.x, r.y);
    const radiusPx = coverageM / (view.worldW / view.cssW);

    // Outer coverage boundary
    ctx.strokeStyle = 'rgba(53, 198, 244, 0.12)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.arc(sx, sy, radiusPx, 0, Math.PI * 2);
    ctx.stroke();

    // Inner strong-signal zone
    const innerPx = radiusPx * 0.4;
    ctx.fillStyle = 'rgba(53, 198, 244, 0.03)';
    ctx.beginPath();
    ctx.arc(sx, sy, innerPx, 0, Math.PI * 2);
    ctx.fill();

    // Animated broadcast pulse (subtle)
    const pulse = (tNow * 2 + parseInt(r.id.replace(/\D/g, '') || 0)) % 3;
    if (pulse < 1) {
      const pulsePx = innerPx + (radiusPx - innerPx) * pulse;
      ctx.strokeStyle = `rgba(53, 198, 244, ${0.15 * (1 - pulse)})`;
      ctx.setLineDash([]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(sx, sy, pulsePx, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  ctx.restore();
}

/* ── RSSI-Colored Peer Links ─────────────────────────────────────────── */

function drawRSSILinks(ctx, view, fleet, posById, tNow) {
  const drawn = new Set();
  ctx.save();

  for (const f of fleet) {
    const a = posById[f.id];
    if (!a) continue;
    for (const peerId of (f.peers || [])) {
      const key = f.id < peerId ? f.id + '|' + peerId : peerId + '|' + f.id;
      if (drawn.has(key)) continue;
      drawn.add(key);
      const b = posById[peerId];
      if (!b) continue;

      const metrics = ManetSim.linkMetrics(f.id, peerId, {robots: Object.values(posById)});
      if (!metrics) continue;

      const [ax, ay] = view.worldToScreen(a.x, a.y);
      const [bx, by] = view.worldToScreen(b.x, b.y);

      // Color based on signal quality
      let colour;
      if (metrics.quality === 'excellent') colour = 'rgba(70, 211, 154, 0.5)';
      else if (metrics.quality === 'good') colour = 'rgba(53, 198, 244, 0.45)';
      else if (metrics.quality === 'fair') colour = 'rgba(245, 184, 67, 0.45)';
      else colour = 'rgba(255, 101, 119, 0.5)';

      // Width based on throughput
      const width = Math.max(0.8, Math.min(3, metrics.rate_mbps / 30));

      ctx.strokeStyle = colour;
      ctx.lineWidth = width;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();

      // RSSI label at midpoint
      if (view.cell >= 18) {
        const mx = (ax + bx) / 2;
        const my = (ay + by) / 2;
        ctx.font = '9px monospace';
        ctx.fillStyle = colour.replace(/[\d.]+\)$/, '0.85)');
        ctx.textAlign = 'center';
        ctx.fillText(`${metrics.rssi_dbm} dBm`, mx, my - 4);
      }
    }
  }
  ctx.setLineDash([]);
  ctx.restore();
}

/* ── Animated Packet Trails ──────────────────────────────────────────── */

const PACKET_TYPE_COLOURS = {
  HB: '#35c6f4', IN: '#46d39a', CL: '#b78cff', RL: '#6f8498',
  YD: '#ff6577', BD: '#f5b843', AW: '#f5b843', TD: '#46d39a',
  TN: '#67e8f9', MB: '#60a5fa',
};

function drawPacketTrails(ctx, view, posById, tNow) {
  if (!ManetSim.isReady()) return;
  const packets = ManetSim.packetsAt(tNow, 0.3);

  ctx.save();
  for (const pkt of packets) {
    const src = posById[pkt.src];
    const dst = posById[pkt.dst];
    if (!src || !dst) continue;

    const [sx, sy] = view.worldToScreen(src.x, src.y);
    const [dx, dy] = view.worldToScreen(dst.x, dst.y);

    // Progress along the link (0→1 over latency)
    const elapsed = (tNow - pkt.t) * 1000; // ms
    const progress = Math.min(1, elapsed / Math.max(1, pkt.latency_ms));

    const px = sx + (dx - sx) * progress;
    const py = sy + (dy - sy) * progress;

    const colour = PACKET_TYPE_COLOURS[pkt.type] || '#35c6f4';

    // Packet dot
    ctx.fillStyle = colour;
    ctx.globalAlpha = pkt.delivered ? (1 - progress * 0.3) : (1 - progress);
    ctx.beginPath();
    ctx.arc(px, py, 3.5, 0, Math.PI * 2);
    ctx.fill();

    // Trail
    ctx.strokeStyle = colour;
    ctx.globalAlpha *= 0.4;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(px, py);
    ctx.stroke();

    // Dropped packet effect
    if (!pkt.delivered && progress > 0.5) {
      ctx.globalAlpha = 0.6 * (1 - progress);
      ctx.strokeStyle = '#ff6577';
      ctx.lineWidth = 2;
      const size = 5;
      ctx.beginPath();
      ctx.moveTo(px - size, py - size);
      ctx.lineTo(px + size, py + size);
      ctx.moveTo(px + size, py - size);
      ctx.lineTo(px - size, py + size);
      ctx.stroke();
    }

    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

/* ── Dead Zone Hatching ──────────────────────────────────────────────── */

function drawDeadZoneHatch(ctx, view, tNow) {
  // Dead zones are already drawn by the existing network.js, but we add
  // a subtle hatching pattern to reinforce the "no radio" semantic.
  // This is intentionally very light to not conflict with the existing visuals.
}

/* ── Node Labels ─────────────────────────────────────────────────────── */

function drawNodeLabels(ctx, view, robots) {
  if (view.cell < 24) return; // too small to read
  ctx.save();
  ctx.font = '8px monospace';
  ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(53, 198, 244, 0.5)';

  for (const r of robots) {
    const [sx, sy] = view.worldToScreen(r.x, r.y);
    const ip = ManetSim.nodeInfo(r.id).ip;
    ctx.fillText(ip, sx, sy + view.cell * 0.55);
  }
  ctx.restore();
}
