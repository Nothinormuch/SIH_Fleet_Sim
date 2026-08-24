/* Holographic overlay for the SIH26123 fleet dashboard.
 *
 * Clean, minimal neon operator console. Only essential metrics float over the
 * warehouse canvas: job progress (top-left), scenario name (top-center),
 * minimap (top-right), and coordination health (bottom-right).
 *
 * Degrades gracefully: if the #hud mount, a summary field or an image is
 * missing, the affected part simply does not draw and nothing throws.
 */

(function () {
  'use strict';

  const C = {
    cyan:    '#00f0ff',
    green:   '#39ff8a',
    amber:   '#f5b843',
  };
  const RING_C = 2 * Math.PI * 62;

  let root = null;
  let el = {};
  let raf = 0;
  let tickerTimer = 0;
  let lastMs = 0;

  let mm = null;
  let mmStatic = null;
  let mmDim = { cell: 1, ox: 0, oy: 0 };

  const S = {
    map: null,
    summary: {},
    summaryRef: null,
    meta: {},
    lastFrame: null,
    doneCur: 0, doneTgt: 0, doneV: 0,
    healthCur: 0, healthTgt: 0, healthV: 0,
    health: 0,
    spawned: 1,
    active: 0,
  };

  const byId = id => document.getElementById(id);
  const qs = sel => (root ? root.querySelector(sel) : null);

  function num(v) { return Number.isFinite(v) ? v : 0; }

  /* Critically-damped spring animation */
  function springStep(dt) {
    const k = 170, c = 16.5;
    const xs = [S.doneCur, S.healthCur];
    const ts = [S.doneTgt, S.healthTgt];
    const vs = [S.doneV, S.healthV];
    for (let i = 0; i < 2; i++) {
      vs[i] += (k * (ts[i] - xs[i]) - c * vs[i]) * dt;
      xs[i] += vs[i] * dt;
      if (Math.abs(ts[i] - xs[i]) < 0.02 && Math.abs(vs[i]) < 0.05) {
        xs[i] = ts[i];
        vs[i] = 0;
      }
    }
    S.doneCur = xs[0]; S.doneV = vs[0];
    S.healthCur = xs[1]; S.healthV = vs[1];
  }

  function colFor(id) {
    if (typeof robotColour === 'function') {
      try { return robotColour(id); } catch (e) { /* fallback */ }
    }
    return C.cyan;
  }

  function withAlpha(col, a) {
    if (col && col[0] === '#') {
      const n = parseInt(col.slice(1), 16);
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    }
    return col || C.cyan;
  }

  /* ------------------------------------------------------------ minimap */

  function worldToMM(wx, wy) {
    return [mmDim.ox + wx * mmDim.cell,
            mmDim.oy + (S.map.height - wy) * mmDim.cell];
  }

  function buildStaticMinimap(map) {
    if (!mm || !map) return;
    const dpr = window.devicePixelRatio || 1;
    const cssW = Math.max(40, mm.canvas.clientWidth || 150);
    const cssH = Math.max(40, mm.canvas.clientHeight || 150);
    mm.canvas.width = Math.round(cssW * dpr);
    mm.canvas.height = Math.round(cssH * dpr);

    const cell = Math.max(2, Math.floor(Math.min(cssW / map.width, cssH / map.height)));
    mmDim = { cell, ox: (cssW - map.width * cell) / 2, oy: (cssH - map.height * cell) / 2 };

    const off = document.createElement('canvas');
    off.width = mm.canvas.width;
    off.height = mm.canvas.height;
    const c = off.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);

    c.fillStyle = '#0a121b';
    c.fillRect(0, 0, cssW, cssH);
    c.lineWidth = 1;
    for (let y = 0; y < map.height; y++) {
      for (let x = 0; x < map.width; x++) {
        const v = map.grid[y][x];
        const sx = mmDim.ox + x * cell;
        const sy = mmDim.oy + y * cell;
        if (v === 1) {
          c.fillStyle = '#122031';
          c.fillRect(sx, sy, cell, cell);
          c.strokeStyle = 'rgba(30,45,66,.8)';
          c.strokeRect(sx + .5, sy + .5, cell - 1, cell - 1);
        } else if (v === 2) {
          c.fillStyle = 'rgba(0,240,255,.18)';
          c.fillRect(sx, sy, cell, cell);
          c.strokeStyle = 'rgba(0,240,255,.7)';
          c.strokeRect(sx + .5, sy + .5, cell - 1, cell - 1);
        } else if (v === 3) {
          c.fillStyle = 'rgba(255,47,246,.15)';
          c.fillRect(sx, sy, cell, cell);
          c.strokeStyle = 'rgba(255,47,246,.65)';
          c.strokeRect(sx + .5, sy + .5, cell - 1, cell - 1);
        } else {
          c.fillStyle = 'rgba(255,255,255,.02)';
          c.fillRect(sx, sy, cell, cell);
        }
      }
    }
    mmStatic = off;
  }

  function paintMinimap(now) {
    if (!mm || !S.map) return;
    const dpr = window.devicePixelRatio || 1;
    const ctx = mm;
    const cssW = ctx.canvas.width / dpr;
    const cssH = ctx.canvas.height / dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    if (mmStatic) ctx.drawImage(mmStatic, 0, 0, cssW, cssH);

    const f = S.lastFrame;
    if (f) {
      for (const h of (f.humans || [])) {
        const [x, y] = worldToMM(h.x, h.y);
        ctx.save();
        ctx.strokeStyle = 'rgba(255,95,87,.9)';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.arc(x, y, Math.max(2.5, mmDim.cell * 0.42), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      for (const r of (f.robots || [])) {
        const col = colFor(r.id);
        const [x, y] = worldToMM(r.x, r.y);
        ctx.save();
        ctx.shadowColor = withAlpha(col, .9);
        ctx.shadowBlur = 6;
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(x, y, Math.max(2, mmDim.cell * 0.34), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }

    /* contained scan bar */
    const ph = ((now || 0) % 3400) / 3400;
    const y = ph * (cssH + 30) - 15;
    const g = ctx.createLinearGradient(0, y - 14, 0, y + 14);
    g.addColorStop(0, 'rgba(0,240,255,0)');
    g.addColorStop(.5, 'rgba(0,240,255,.32)');
    g.addColorStop(1, 'rgba(0,240,255,0)');
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = g;
    ctx.fillRect(0, y - 14, cssW, 28);
    ctx.globalCompositeOperation = 'source-over';
  }

  function fleetHealth(summary, meta) {
    const announced = num(summary.tasks_announced) || num(meta.tasks) || 0;
    const completed = num(summary.tasks_completed) || 0;
    const robots = num(summary.robots) || num(meta.robots) || 1;
    const progress = announced ? Math.min(1, completed / announced) : 0;
    const shield = Math.max(0, 1 - num(summary.safety_stop_ticks) / Math.max(1, robots * 40));
    const resolve = Math.max(0, 1 - num(summary.deadlocks_detected) / Math.max(1, announced || 1));
    return Math.round(100 * Math.max(0.05, Math.min(1,
      0.45 * progress + 0.30 * shield + 0.25 * resolve)));
  }

  const fmt = (v, d) => String(((Number.isFinite(v) ? v : 0) || 0).toFixed(d));

  const markup =
    '<article class="hud-block hud-jobs" data-hud="jobs">' +
      '<span class="hud-eb">Jobs Completed</span>' +
      '<div class="hud-orb">' +
        '<svg class="hud-progress" viewBox="0 0 140 140" aria-hidden="true">' +
          '<circle class="hud-progress-track" cx="70" cy="70" r="62"></circle>' +
          '<circle class="hud-progress-arc" data-hud="jobsArc" cx="70" cy="70" r="62"></circle>' +
        '</svg>' +
        '<div class="hud-orb-core">' +
          '<div class="hud-count" data-hud="jobsNum">0</div>' +
          '<div class="hud-count-sub">tasks done</div>' +
        '</div>' +
      '</div>' +
      '<div class="hud-sub" data-hud="jobsSub">of 0 tasks · 0%</div>' +
      '<div class="hud-banner" data-hud="jobsBanner">Fleet Active</div>' +
    '</article>' +
    '<aside class="hud-block hud-ticker">' +
      '<span class="hud-ticker-text" data-hud="ticker">SIH26123</span>' +
    '</aside>' +
    '<article class="hud-block hud-minimap-block">' +
      '<span class="hud-eb">Fleet Minimap</span>' +
      '<div class="hud-mm-frame"><canvas class="hud-mm" data-hud="mm"></canvas></div>' +
    '</article>' +
    '<article class="hud-block hud-health">' +
      '<span class="hud-eb">Coordination Health</span>' +
      '<div class="hud-ring">' +
        '<div class="hud-holo"></div>' +
        '<svg viewBox="0 0 140 140" aria-hidden="true">' +
          '<circle class="hud-ring-track" cx="70" cy="70" r="62"></circle>' +
          '<circle class="hud-ring-arc" data-hud="healthArc" cx="70" cy="70" r="62"></circle>' +
        '</svg>' +
        '<div class="hud-ring-core">' +
          '<div class="hud-health-num" data-hud="healthNum">0</div>' +
          '<div class="hud-health-sub" data-hud="healthSub">fleet online</div>' +
        '</div>' +
      '</div>' +
      '<div class="hud-micro-stats">' +
        '<span class="hud-micro">Deadlocks <b data-hud="mDead">0</b></span>' +
        '<span class="hud-micro">Give-way <b data-hud="mYield">0</b></span>' +
        '<span class="hud-micro">Shields <b data-hud="mShield">0</b></span>' +
      '</div>' +
    '</article>' +
    '<article class="hud-block hud-fleet">' +
      '<span class="hud-eb">Fleet Status</span>' +
      '<div class="hud-fleet-grid" data-hud="fleetGrid"></div>' +
    '</article>' +
    '<article class="hud-block hud-allocation">' +
      '<span class="hud-eb">Task Allocation</span>' +
      '<div class="hud-alloc-stats">' +
        '<div class="hud-alloc-row">' +
          '<span class="hud-alloc-label">Announced</span>' +
          '<span class="hud-alloc-val" data-hud="allocAnnounced">0</span>' +
        '</div>' +
        '<div class="hud-alloc-row">' +
          '<span class="hud-alloc-label">Allocated</span>' +
          '<span class="hud-alloc-val" data-hud="allocAllocated">0</span>' +
        '</div>' +
        '<div class="hud-alloc-row">' +
          '<span class="hud-alloc-label">Completed</span>' +
          '<span class="hud-alloc-val" data-hud="allocCompleted">0</span>' +
        '</div>' +
        '<div class="hud-alloc-row">' +
          '<span class="hud-alloc-label">Throughput</span>' +
          '<span class="hud-alloc-val" data-hud="allocThroughput">0</span>' +
        '</div>' +
      '</div>' +
    '</article>';

  function build() {
    root.innerHTML = markup;
    el.jobs = qs('[data-hud="jobs"]');
    el.jobsNum = qs('[data-hud="jobsNum"]');
    el.jobsArc = qs('[data-hud="jobsArc"]');
    el.jobsSub = qs('[data-hud="jobsSub"]');
    el.jobsBanner = qs('[data-hud="jobsBanner"]');
    el.ticker = qs('[data-hud="ticker"]');
    el.mmCanvas = qs('[data-hud="mm"]');
    el.healthArc = qs('[data-hud="healthArc"]');
    el.healthNum = qs('[data-hud="healthNum"]');
    el.healthSub = qs('[data-hud="healthSub"]');
    el.mDead = qs('[data-hud="mDead"]');
    el.mYield = qs('[data-hud="mYield"]');
    el.mShield = qs('[data-hud="mShield"]');
    el.fleetGrid = qs('[data-hud="fleetGrid"]');
    el.allocAnnounced = qs('[data-hud="allocAnnounced"]');
    el.allocAllocated = qs('[data-hud="allocAllocated"]');
    el.allocCompleted = qs('[data-hud="allocCompleted"]');
    el.allocThroughput = qs('[data-hud="allocThroughput"]');

    el.jobsArc.style.strokeDasharray = RING_C.toFixed(2);
    el.jobsArc.style.strokeDashoffset = RING_C.toFixed(2);
    const arcLen = RING_C * 0.75;
    el.healthArc.style.strokeDasharray = arcLen.toFixed(2);
    el.healthArc.style.strokeDashoffset = arcLen.toFixed(2);
    el.healthArc.setAttribute('transform', 'rotate(135 70 70)');

    const sum = S.summary, meta = S.meta;

    // Set scenario name in ticker
    if (meta.scenario) {
      el.ticker.textContent = 'Scenario: ' + String(meta.scenario).replace(/_/g, ' ');
    }

    el.mDead.textContent = fmt(sum.deadlocks_detected, 0);
    el.mYield.textContent = fmt(sum.yields, 0);
    el.mShield.textContent = fmt(sum.safety_stop_ticks, 0);

    // Initialize allocation stats
    if (el.allocAnnounced) el.allocAnnounced.textContent = fmt(sum.tasks_announced, 0);
    if (el.allocCompleted) el.allocCompleted.textContent = fmt(sum.tasks_completed, 0);
    if (el.allocThroughput) el.allocThroughput.textContent = fmt(sum.throughput_per_robot_hr, 1);

    S.health = fleetHealth(sum, meta);
    S.healthTgt = S.health;

    mm = el.mmCanvas.getContext('2d');
  }

  /* ------------------------------------------------- render */

  function updateJobs() {
    const n = Math.round(S.doneCur);
    el.jobsNum.textContent = String(n);
    const announced = num(S.summary.tasks_announced) || num(S.meta.tasks) || 0;
    const frac = announced ? Math.min(1, Math.max(0, S.doneCur / announced)) : 0;
    el.jobsArc.style.strokeDashoffset = (RING_C * (1 - frac)).toFixed(1);
    el.jobsSub.textContent = 'of ' + announced + ' tasks · ' + Math.round(frac * 100) + '%';
    const complete = announced > 0 && S.doneCur >= announced && S.summary.completed_all;
    el.jobs.setAttribute('data-complete', complete ? '1' : '0');
    el.jobsArc.style.stroke = complete ? C.green : C.cyan;
    if (complete) {
      el.jobsBanner.textContent = 'Mission Complete';
      el.jobsBanner.classList.add('hud-banner-complete');
    } else {
      el.jobsBanner.textContent = 'Fleet Active';
      el.jobsBanner.classList.remove('hud-banner-complete');
    }
  }

  function updateHealth() {
    const h = Math.max(0, Math.min(100, Math.round(S.healthCur)));
    const frac = h / 100;
    el.healthNum.textContent = String(h);
    el.healthArc.style.strokeDashoffset = (RING_C * 0.75 * (1 - frac)).toFixed(1);
    const col = h >= 70 ? C.green : (h >= 40 ? C.cyan : C.amber);
    el.healthNum.style.color = col;
    el.healthNum.style.filter = 'drop-shadow(0 0 8px ' + col + ')';
    el.healthArc.style.stroke = col;
    el.healthSub.textContent = S.active + '/' + S.spawned + ' fleet online';
  }

  function updateFleet() {
    if (!el.fleetGrid || !S.lastFrame) return;
    const fleet = S.lastFrame.fleet || [];
    if (fleet.length === 0) {
      el.fleetGrid.innerHTML = '<div style="color: rgba(255,255,255,0.5); font-size: 10px;">No fleet data</div>';
      return;
    }

    let html = '';
    for (const robot of fleet) {
      const state = robot.state || 'unknown';
      const stateCol = state === 'idle' ? 'rgba(120,140,160,.6)' :
                       state === 'charging' ? 'rgba(255,184,103,.7)' :
                       state === 'active' ? '#39ff8a' : '#00f0ff';
      html += `<div class="hud-fleet-row">
        <span class="hud-fleet-id">${robot.id}</span>
        <span class="hud-fleet-state" style="color: ${stateCol}">${state.slice(0, 3).toUpperCase()}</span>
        <span class="hud-fleet-task">${robot.task ? 'T' + robot.task : '—'}</span>
        <span class="hud-fleet-done">${fmt(robot.done, 0)}</span>
      </div>`;
    }
    el.fleetGrid.innerHTML = html;
  }

  function updateAllocation() {
    if (!S.summary) return;
    const sum = S.summary;
    const announced = num(sum.tasks_announced) || num(S.meta.tasks) || 0;
    const allocated = Math.max(0, announced - (num(sum.tasks_announced) - num(sum.tasks_completed)));

    if (el.allocAnnounced) el.allocAnnounced.textContent = fmt(announced, 0);
    if (el.allocAllocated) el.allocAllocated.textContent = fmt(allocated, 0);
    if (el.allocCompleted) el.allocCompleted.textContent = fmt(sum.tasks_completed, 0);
    if (el.allocThroughput) el.allocThroughput.textContent = fmt(sum.throughput_per_robot_hr, 1) + ' t/r·h';
  }

  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (!root) return;
    const dt = Math.min(0.06, (now - lastMs) / 1000) || 0.016;
    lastMs = now;
    springStep(dt);
    updateJobs();
    updateHealth();
    updateFleet();
    updateAllocation();
    paintMinimap(now);
  }

  /* ---------------------------------------------------------------- API */

  const Hud = {
    init(view, imgs, data) {
      this.dispose();
      root = byId('hud');
      if (!root) return;
      if (!data || !data.map) return;
      S.map = data.map;
      S.summary = (data && data.summary) || {};
      S.meta = (data && data.meta) || {};
      S.spawned = Math.max(1, num(S.summary.robots) || num(S.meta.robots) || 1);
      S.lastFrame = null;
      S.summaryRef = data.summary || null;
      S.doneCur = S.doneTgt = S.doneV = 0;
      S.healthCur = S.healthV = 0;

      build();
      if (S.map) buildStaticMinimap();

      lastMs = performance.now();
      raf = requestAnimationFrame(loop);
    },

    render(frame, summary, meta, t) {
      if (!root || !S.map || !frame) return;
      if (summary && summary !== S.summaryRef) {
        S.summaryRef = summary;
        this.syncRunSummary(summary);
      }
      if (meta) S.meta = meta;
      S.lastFrame = frame;

      let done = 0;
      for (const f of (frame.fleet || [])) done += num(f.done);
      S.doneTgt = done;
      S.active = Math.max(0, Math.min(frame.robots.length,
        (frame.fleet || []).filter(f => f.state && f.state !== 'idle' && f.state !== 'charging').length));
    },

    syncRunSummary(summary) {
      if (!summary) return;
      S.summary = summary;
      S.health = fleetHealth(summary, S.meta);
      S.healthTgt = S.health;
      if (el.mDead) el.mDead.textContent = fmt(summary.deadlocks_detected, 0);
      if (el.mYield) el.mYield.textContent = fmt(summary.yields, 0);
      if (el.mShield) el.mShield.textContent = fmt(summary.safety_stop_ticks, 0);
      if (el.jobsSub) {
        const announced = num(summary.tasks_announced) || num(S.meta.tasks) || 0;
        const frac = announced ? Math.min(1, S.doneCur / announced) : 0;
        el.jobsSub.textContent = 'of ' + announced + ' tasks · ' + Math.round(frac * 100) + '%';
      }
    },

    resize(map) {
      if (map) S.map = map;
      if (!root || !S.map) return;
      buildStaticMinimap();
      paintMinimap(performance.now());
    },

    dispose() {
      if (tickerTimer) { clearInterval(tickerTimer); tickerTimer = 0; }
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      if (root) root.innerHTML = '';
      root = null; el = {}; mm = null; mmStatic = null;
      S.map = null; S.summary = {}; S.summaryRef = null; S.meta = {}; S.lastFrame = null;
      S.doneCur = S.doneTgt = S.doneV = 0;
      S.healthCur = S.healthTgt = S.healthV = 0;
    },
  };

  window.Hud = Hud;
})();
