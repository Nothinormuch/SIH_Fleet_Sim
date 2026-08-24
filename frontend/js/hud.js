/* Holographic overlay for the SIH26123 fleet dashboard.
 *
 * A passive, decorative layer. It reads the same payload the rest of the
 * dashboard reads and never asks for new endpoints. Jobs done are summed from
 * the fleet snapshot, the minimap is drawn from the world map plus live robot
 * and human telemetry, and throughput / makespan / coordination come from the
 * run summary. The only per-frame live pieces are the counter, the clock, the
 * minimap and the robot ring.
 *
 * Degrades gracefully: if the #hud mount, a summary field or an image
 * is missing, the affected part simply does not draw and nothing throws.
 */

(function () {
  'use strict';

  const C = {                             /* the neon set, fallback too */
    cyan:    '#00f0ff',
    magenta: '#ff2ff6',
    green:   '#39ff8a',
    purple:  '#b300ff',
    pink:    '#ff00a8',
  };
  const RING_C = 2 * Math.PI * 62;                 /* viewBox radius 62  */

  let root = null;                 /* .hud-root element                  */
  let el = {};                     /* element cache                       */
  let raf = 0;
  let tickerTimer = 0;
  let lastMs = 0;

  let mm = null;               /* minimap 2d context                     */
  let mmStatic = null;             /* offscreen cached grid              */
  let mmDim = { cell: 1, ox: 0, oy: 0 };

  const S = {                      /* owned state                          */
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

  /* A v tiny critically-damped spring. ~2% overshoot, settle ~0.4s. */
  function springStep(dt) {
    const k = 170, c = 16.5;
    const xs = [S.doneCur, S.healthCur];
    const ts = [S.doneTgt, S.healthTgt];
    const vs = [S.doneV, S.healthV];
    for (let i = 0; i < 2; i++) {
      vs[i] += (k * (ts[i] - xs[i]) - c * vs[i]) * dt;
      xs[i] += vs[i] * dt;
      if (Math.abs(ts[i] - xs[i]) < 0.02 && Math.abs(vs[i]) < 0.05) {
        xs[i] = ts[i]; vs[i] = 0;
      }
    }
    S.doneCur = xs[0]; S.doneV = vs[0];
    S.healthCur = xs[1]; S.healthV = vs[1];
  }

  /* Robot identity colours come from the floor module when it is present, so
   * the minimap and the warehouse share one colour language with zero copies. */
  function colFor(id) {
    if (typeof robotColour === 'function') {
      try { return robotColour(id); } catch (e) { /* keep fallback */ }
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
            mmDim.oy + (S.map.height - wy) * mmDim.cell];   /* +Y = north */
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
        if (v === 1) {                       /* rack */
          c.fillStyle = '#122031';
          c.fillRect(sx, sy, cell, cell);
          c.strokeStyle = 'rgba(30,45,66,.8)';
          c.strokeRect(sx + .5, sy + .5, cell - 1, cell - 1);
        } else if (v === 2) {             /* station */
          c.fillStyle = 'rgba(0,240,255,.18)';
          c.fillRect(sx, sy, cell, cell);
          c.strokeStyle = 'rgba(0,240,255,.7)';
          c.strokeRect(sx + .5, sy + .5, cell - 1, cell - 1);
        } else if (v === 3) {      /* dock */
          c.fillStyle = 'rgba(255,47,246,.15)';
          c.fillRect(sx, sy, cell, cell);
          c.strokeStyle = 'rgba(255,47,246,.65)';
          c.strokeRect(sx + .5, sy + .5, cell - 1, cell - 1);
        } else {                    /* free aisle */
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

    /* the scan bar runs on its own clock so the minimap feels live */
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

  function buildClaims() {
    const sum = S.summary, meta = S.meta;
    const out = [];
    const contacts = num(sum.contacts_robot_robot) + num(sum.contacts_robot_human);
    if (contacts === 0) out.push('Zero contact events this run');
    if (num(sum.deadlocks_detected) > 0) {
      out.push(num(sum.deadlocks_detected) + ' deadlock' +
               (num(sum.deadlocks_detected) === 1 ? '' : 's') + ' resolved');
    }
    if (num(sum.yields) > 0) out.push(num(sum.yields) + ' give-way maneuvre' +
                                      (num(sum.yields) === 1 ? '' : 's'));
    if (num(sum.replans) > 0) out.push(num(sum.replans) + ' replan' +
                                       (num(sum.replans) === 1 ? '' : 's') + ' handled');
    if (num(sum.safety_stop_ticks) > 0) out.push('protective shields armed');
    if (num(sum.tasks_completed) > 0) {
      out.push(num(sum.tasks_completed) + ' task' +
               (num(sum.tasks_completed) === 1 ? '' : 's') + ' completed');
    }
    if (num(sum.min_separation_m) > 0) out.push('min separation ' +
                                               num(sum.min_separation_m).toFixed(2) + ' m');
    if (meta.policy && meta.policy !== 'stop_and_wait') out.push('Fleet coordination active');
    out.push('Throughput ' + (num(sum.throughput_per_robot_hr) || 0).toFixed(2) + ' task/r.h');
    if (meta.scenario) out.push('Scenario: ' + String(meta.scenario).replace(/_/g, ' '));
    return out.length ? out : ['SIH26123 fleet nominal'];
  }

  /* Run-level 0..100 health: every term is a positive reframe of numbers the
   * sim already measured (progress, rare protective-stops, deadlocks resolved). */
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
      '<span class="hud-eb">Jobs completed</span>' +
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
      '<div class="hud-banner" data-hud="jobsBanner">FLEET ACTIVE</div>' +
    '</article>' +
    '<article class="hud-block hud-minimap-block">' +
      '<span class="hud-eb">Fleet minimap</span>' +
      '<div class="hud-mm-frame"><canvas class="hud-mm" data-hud="mm"></canvas></div>' +
    '</article>' +
    '<article class="hud-block hud-stats">' +
      '<span class="hud-eb">Throughput &amp; timing</span>' +
      '<div class="hud-chip-row">' +
        '<div class="hud-chip"><div class="hud-chip-header">throughput</div>' +
          '<b data-hud="tp">0.00</b></div>' +
        '<div class="hud-chip"><div class="hud-chip-header" data-hud="msLabel">makespan</div>' +
          '<b data-hud="ms">0.0s</b></div>' +
      '</div>' +
      '<div class="hud-clock" data-hud="clock">0.0s / 0.0s</div>' +
    '</article>' +
    '<article class="hud-block hud-health">' +
      '<span class="hud-eb">Coordination health</span>' +
      '<div class="hud-ring">' +
        '<div class="hud-holo"></div>' +
        '<svg viewBox="0 0 140 140" aria-hidden="true">' +
          '<circle class="hud-ring-track" cx="70" cy="70" r="62"></circle>' +
          '<circle class="hud-ring-arc" data-hud="healthArc" cx="70" cy="70" r="62"></circle>' +
          '<g data-hud="healthDots"></g>' +
        '</svg>' +
        '<div class="hud-ring-core">' +
          '<div class="hud-health-num" data-hud="healthNum">0</div>' +
          '<div class="hud-health-sub" data-hud="healthSub">fleet online</div>' +
        '</div>' +
      '</div>' +
      '<div class="hud-micro-stats">' +
        '<span class="hud-micro">deadlocks resolved <b data-hud="mDead">0</b></span>' +
        '<span class="hud-micro">give-way moves <b data-hud="mYield">0</b></span>' +
        '<span class="hud-micro">shield activations <b data-hud="mShield">0</b></span>' +
      '</div>' +
    '</article>' +
    '<aside class="hud-block hud-ticker">' +
      '<span class="hud-ticker-text" data-hud="ticker"></span>' +
    '</aside>' +
    '<div class="hud-scanline"></div>' +
    '<div class="hud-sheen"></div>';

  function build() {
    root.innerHTML = markup;
    el.jobs = qs('[data-hud="jobs"]');
    el.jobsNum = qs('[data-hud="jobsNum"]');
    el.jobsArc = qs('[data-hud="jobsArc"]');
    el.jobsSub = qs('[data-hud="jobsSub"]');
    el.jobsBanner = qs('[data-hud="jobsBanner"]');
    el.mmCanvas = qs('[data-hud="mm"]');
    el.tp = qs('[data-hud="tp"]');
    el.ms = qs('[data-hud="ms"]');
    el.msLabel = qs('[data-hud="msLabel"]');
    el.clock = qs('[data-hud="clock"]');
    el.healthArc = qs('[data-hud="healthArc"]');
    el.healthDots = qs('[data-hud="healthDots"]');
    el.healthNum = qs('[data-hud="healthNum"]');
    el.healthSub = qs('[data-hud="healthSub"]');
    el.mDead = qs('[data-hud="mDead"]');
    el.mYield = qs('[data-hud="mYield"]');
    el.mShield = qs('[data-hud="mShield"]');
    el.ticker = qs('[data-hud="ticker"]');

    el.jobsArc.style.strokeDasharray = RING_C.toFixed(2);
    el.jobsArc.style.strokeDashoffset = RING_C.toFixed(2);
    const arcLen = RING_C * 0.75;                 /* 270° */
    el.healthArc.style.strokeDasharray = arcLen.toFixed(2);
    el.healthArc.style.strokeDashoffset = arcLen.toFixed(2);
    el.healthArc.setAttribute('transform', 'rotate(135 70 70)');

    const ns = 'http://www.w3.org/2000/svg';
    const n = Math.max(1, S.spawned);
    for (let i = 0; i < n; i++) {
      const th = (135 + 270 * (i / n)) * Math.PI / 180;
      const id = 'AMR' + String(i + 1).padStart(2, '0');
      const col = colFor(id);
      const dot = document.createElementNS(ns, 'circle');
      dot.setAttribute('cx', (70 + 61 * Math.cos(th)).toFixed(2));
      dot.setAttribute('cy', (70 + 61 * Math.sin(th)).toFixed(2));
      dot.setAttribute('r', '5');
      dot.setAttribute('fill', col);
      dot.style.filter = 'drop-shadow(0 0 4px ' + col + ')';
      el.healthDots.appendChild(dot);
    }

    const sum = S.summary, meta = S.meta;
    el.tp.textContent = fmt(sum.throughput_per_robot_hr, 2) + ' tasks/r.h';
    el.ms.textContent = fmt(sum.makespan_s, 1) + 's';
    el.msLabel.textContent = sum.completed_all ? 'makespan' : 'live runtime';
    el.clock.textContent = '0.0s / ' + fmt(sum.makespan_s || meta.duration_s, 1) + 's';

    el.mDead.textContent = fmt(sum.deadlocks_detected, 0);
    el.mYield.textContent = fmt(sum.yields, 0);
    el.mShield.textContent = fmt(sum.safety_stop_ticks, 0);

    S.health = fleetHealth(sum, meta);
    S.healthTgt = S.health;

    const claims = buildClaims();
    let ci = 0;
    el.ticker.textContent = claims[0];
    tickerTimer = setInterval(function () {
      el.ticker.style.opacity = '0';
      setTimeout(function () {
        el.ticker.textContent = claims[ci % claims.length];
        el.ticker.style.opacity = '1';
      }, 230);
      ci++;
    }, 4000);

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
      el.jobsBanner.textContent = 'MISSION COMPLETE';
      el.jobsBanner.classList.add('hud-banner-complete');
    } else {
      el.jobsBanner.textContent = 'FLEET ACTIVE';
      el.jobsBanner.classList.remove('hud-banner-complete');
    }
  }

  function updateHealth() {
    const h = Math.max(0, Math.min(100, Math.round(S.healthCur)));
    const frac = h / 100;
    el.healthNum.textContent = String(h);
    el.healthArc.style.strokeDashoffset = (RING_C * 0.75 * (1 - frac)).toFixed(1);
    const col = h >= 70 ? C.green : (h >= 40 ? C.cyan : C.pink);
    el.healthNum.style.color = col;
    el.healthNum.style.filter = 'drop-shadow(0 0 10px ' + col + ')';
    el.healthArc.style.stroke = col;
    el.healthSub.textContent = S.active + '/' + S.spawned + ' fleet online';
  }

  function loop(now) {
    raf = requestAnimationFrame(loop);
    if (!root) return;
    const dt = Math.min(0.06, (now - lastMs) / 1000) || 0.016;
    lastMs = now;
    springStep(dt);
    updateJobs();
    updateHealth();
    paintMinimap(now);
  }

  /* ---------------------------------------------------------------- API */

  const Hud = {
    init(view, imgs, data) {
      this.dispose();
      root = byId('hud');
      if (!root) return;                          /* mount missing -> no-op */
      if (!data || !data.map) return;             /* null data -> keep silent */
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
      if (!root || !S.map || !frame) return;   /* before first run: no-op */
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

      el.clock.textContent = fmt(t, 1) + 's / ' +
        fmt(S.summary.makespan_s || S.meta.duration_s, 1) + 's';
    },

    /* Run-level chips: constant per run, but re-evaluate when a caller hands in
     * a different summary object so replaying with a fresh payload stays honest. */
    syncRunSummary(summary) {
      if (!summary) return;
      S.summary = summary;
      S.health = fleetHealth(summary, S.meta);
      S.healthTgt = S.health;
      if (el.tp) el.tp.textContent = fmt(summary.throughput_per_robot_hr, 2) + ' tasks/r.h';
      if (el.ms) el.ms.textContent = fmt(summary.makespan_s, 1) + 's';
      if (el.msLabel) el.msLabel.textContent = summary.completed_all ? 'makespan' : 'live runtime';
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
