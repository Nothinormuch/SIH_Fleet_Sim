/* App shell: fetch a run, play it back, keep the panel in sync.
 *
 * Playback interpolates between telemetry frames. Telemetry arrives at 10 Hz while the
 * physics ran at 50 Hz, so drawing frames verbatim would look like a stop-motion film of
 * something that is actually smooth. Interpolating positions (and slerping headings, so
 * a robot crossing +/-pi does not spin the long way round) shows the motion the
 * simulation really produced.
 */

const el = id => document.getElementById(id);

const App = {
  view: null,
  imgs: null,
  data: null,        // { map, meta, frames, summary }
  staticLayer: null,
  playing: false,
  simTime: 0,
  lastRaf: 0,
  speed: 1,
  auctionEvents: [],
};

/* ------------------------------------------------------------------ boot */

async function boot() {
  App.view = new View(el('floor'));
  App.imgs = await loadAssets();

  try {
    const r = await fetch('/api/scenarios');
    const { scenarios, policies } = await r.json();
    fill(el('scenario'), scenarios, 'crossing_chokepoint');
    fill(el('policy'), policies, 'hierarchical');
  } catch (e) {
    setStatus('Could not reach the server. Is backend/server.py running?', 'err');
  }

  el('runBtn').addEventListener('click', run);
  el('playBtn').addEventListener('click', togglePlay);
  el('speed').addEventListener('change', e => { App.speed = parseFloat(e.target.value); });
  el('scrub').addEventListener('input', e => {
    if (!App.data) return;
    App.playing = false;
    el('playBtn').textContent = 'Play';
    App.simTime = frameTime(parseInt(e.target.value, 10));
    draw();
  });
  window.addEventListener('keydown', e => {
    if (e.code === 'Space' && e.target.tagName !== 'INPUT'
        && e.target.tagName !== 'SELECT') {
      e.preventDefault();
      togglePlay();
    }
  });
  window.addEventListener('resize', () => {
    if (!App.data) return;
    App.view.resize(App.data.map);
    App.staticLayer = buildStaticLayer(App.view, App.data.map, App.imgs);
    draw();
  });

  requestAnimationFrame(tick);
  run();                                     // something on screen without a click
}

function fill(select, values, preferred) {
  select.innerHTML = '';
  for (const v of values) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = v.replace(/_/g, ' ');
    if (v === preferred) o.selected = true;
    select.appendChild(o);
  }
}

/* ------------------------------------------------------------------ running */

async function run() {
  const q = new URLSearchParams({
    scenario: el('scenario').value,
    policy: el('policy').value,
    robots: el('robots').value,
    seed: el('seed').value,
    duration: el('duration').value,
  });
  el('runBtn').disabled = true;
  App.playing = false;
  el('playBtn').textContent = 'Play';
  setStatus(`Simulating ${el('duration').value}s of ${el('scenario').value}…`, 'busy');

  try {
    const res = await fetch('/api/run?' + q.toString());
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);

    App.data = payload;
    App.auctionEvents = payload.frames.flatMap(f => f.auction_events || []);
    App.simTime = 0;
    App.view.resize(payload.map);
    App.staticLayer = buildStaticLayer(App.view, payload.map, App.imgs);

    const n = payload.frames.length;
    el('scrub').max = Math.max(0, n - 1);
    el('scrub').value = 0;
    el('clockEnd').textContent = (n ? payload.frames[n - 1].t : 0).toFixed(1);

    renderSummary(payload.summary, payload.meta);
    setStatus(`${n} frames · ${payload.meta.robots} robots · seed ${payload.meta.seed}`);
    draw();
    togglePlay();
  } catch (e) {
    setStatus('Run failed: ' + e.message, 'err');
  } finally {
    el('runBtn').disabled = false;
  }
}

function setStatus(text, cls) {
  const s = el('status');
  s.textContent = text;
  s.className = 'status' + (cls ? ' ' + cls : '');
}

/* ------------------------------------------------------------------ playback */

function togglePlay() {
  if (!App.data || !App.data.frames.length) return;
  App.playing = !App.playing;
  el('playBtn').textContent = App.playing ? 'Pause' : 'Play';
  if (App.playing && App.simTime >= endTime()) App.simTime = 0;
  App.lastRaf = performance.now();
}

const endTime = () => {
  const f = App.data.frames;
  return f.length ? f[f.length - 1].t : 0;
};
const frameTime = i => {
  const f = App.data.frames;
  return f.length ? f[Math.max(0, Math.min(i, f.length - 1))].t : 0;
};

function tick(now) {
  requestAnimationFrame(tick);
  if (!App.data || !App.playing) { App.lastRaf = now; return; }

  const dt = Math.min(0.25, (now - App.lastRaf) / 1000);
  App.lastRaf = now;
  App.simTime += dt * App.speed;

  if (App.simTime >= endTime()) {
    App.simTime = endTime();
    App.playing = false;
    el('playBtn').textContent = 'Play';
  }
  draw();
}

/* Which two frames bracket the current sim time, and how far between them are we?
 * Frames are evenly spaced, so this is arithmetic rather than a search. */
function bracket(t) {
  const f = App.data.frames;
  if (f.length < 2) return [f[0], f[0], 0, 0];
  const step = f[1].t - f[0].t;
  const raw = t / step;
  const i = Math.max(0, Math.min(f.length - 2, Math.floor(raw)));
  return [f[i], f[i + 1], Math.max(0, Math.min(1, raw - i)), i];
}

const lerp = (a, b, u) => a + (b - a) * u;

/* Shortest-arc interpolation. Without it a robot whose heading crosses +/-pi appears to
 * spin a full turn in one frame. */
function lerpAngle(a, b, u) {
  let d = b - a;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a + d * u;
}

function interpolate(f0, f1, u) {
  const byId = {};
  for (const r of f1.robots) byId[r.id] = r;
  const robots = f0.robots.map(a => {
    const b = byId[a.id] || a;
    return {
      id: a.id,
      x: lerp(a.x, b.x, u),
      y: lerp(a.y, b.y, u),
      th: lerpAngle(a.th, b.th, u),
      batt: lerp(a.batt, b.batt, u),
      carry: a.carry,
    };
  });
  const hById = {};
  for (const h of (f1.humans || [])) hById[h.id] = h;
  const humans = (f0.humans || []).map(a => {
    const b = hById[a.id] || a;
    // Humans broadcast nothing, so the telemetry carries no heading for them - it is
    // derived from where they moved between frames, which is exactly the information a
    // robot's own tracker would have. Below a threshold, keep the last heading rather
    // than letting sub-pixel jitter spin the sprite.
    const dx = b.x - a.x, dy = b.y - a.y;
    let th = a._th;
    if (Math.hypot(dx, dy) > 1e-3) th = Math.atan2(dy, dx);
    a._th = th;
    return { id: a.id, x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), th: th };
  });
  // Discrete state is taken from the earlier frame, never blended: a robot is either
  // blocked or it is not, and averaging a state string is meaningless.
  return { t: lerp(f0.t, f1.t, u), robots, humans, fleet: f0.fleet,
           manager_alive: f0.manager_alive, contacts: f0.contacts,
           auction_events: f0.auction_events || [] };
}

/* ------------------------------------------------------------------ drawing */

function draw() {
  if (!App.data || !App.data.frames.length) return;
  const [f0, f1, u, idx] = bracket(App.simTime);
  const frame = interpolate(f0, f1, u);

  const { ctx } = App.view;
  App.view.clear();
  if (App.staticLayer) {
    ctx.drawImage(App.staticLayer, 0, 0, App.view.cssW, App.view.cssH);
  }
  drawNetwork(ctx, App.view, frame, App.imgs, frame.t);
  drawFleet(ctx, App.view, frame, App.imgs, { labels: App.view.cell >= 22 });

  el('scrub').value = idx;
  el('clockNow').textContent = frame.t.toFixed(1);
  updateManagerDot(frame);
  renderFleetPanel(frame);
  renderAuctionPanel(frame);
}

function updateManagerDot(frame) {
  const dot = el('mgrDot');
  const text = el('mgrText');
  const policy = App.data.meta.policy;
  if (policy === 'decentralized') {
    dot.className = 'dot p2p';
    text.textContent = 'WMS injector · peer auction';
    return;
  }
  if (policy === 'stop_and_wait' || policy === 'BIOS_1.0.0') {
    dot.className = 'dot';
    text.textContent = policy === 'BIOS_1.0.0'
      ? 'no fleet manager · peer traffic'
      : 'no fleet manager (baseline)';
    return;
  }
  const alive = frame.manager_alive;
  dot.className = 'dot ' + (alive ? 'up' : 'down');
  text.textContent = alive ? 'fleet manager reachable'
                           : 'fleet manager DOWN · fleet on peer-to-peer';
}

/* ------------------------------------------------------------------ panel */

function renderFleetPanel(frame) {
  const info = {};
  for (const f of (frame.fleet || [])) info[f.id] = f;

  const rows = frame.robots.map(r => {
    const f = info[r.id] || {};
    const colour = robotColour(r.id);
    const batt = Math.round((r.batt || 0) * 100);
    const battCls = batt < 15 ? 'crit' : (batt < 35 ? 'low' : '');
    const waiting = f.blocked_on
      ? (f.blocked_on === 'gate' ? 'awaiting block' : 'waiting on ' + f.blocked_on)
      : (f.task ? 'task ' + f.task : 'unassigned');

    return `
      <div class="robot" style="border-left-color:${colour}">
        <span class="swatch" style="background:${colour}"></span>
        <div>
          <div class="rid">${r.id}</div>
          <div class="meta">${waiting}</div>
          <div class="batt"><i class="${battCls}" style="width:${batt}%"></i></div>
        </div>
        <div class="right">
          <span class="state ${f.state || 'idle'}">${(f.state || 'idle').replace(/_/g, ' ')}</span>
          <div class="meta" style="margin-top:5px">${f.done || 0} done</div>
        </div>
      </div>`;
  });
  el('fleet').innerHTML = rows.join('');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function uniqueAuctionEvents(events) {
  const awardKeys = new Set();
  return events.filter(event => {
    if (event.type !== 'AW') return true;
    // The winner rebroadcasts AWARD to renew its lease. It is real wire traffic,
    // but it is not a new allocation, so keep one visible row per task/epoch/winner.
    const key = [event.task, event.e ?? 0,
      event.winner || event.dst || event.src].join('|');
    if (awardKeys.has(key)) return false;
    awardKeys.add(key);
    return true;
  });
}

function renderAuctionPanel(frame) {
  const summary = el('auctionSummary');
  const log = el('auctionLog');
  if (!summary || !log) return;

  const policy = App.data.meta.policy;
  const events = App.auctionEvents.filter(e => e.t <= frame.t + 1e-6);
  const visibleEvents = uniqueAuctionEvents(events);
  const counts = { TN: 0, BD: 0, AW: 0, TD: 0 };
  for (const event of visibleEvents) {
    if (counts[event.type] !== undefined) counts[event.type]++;
  }
  const renewals = events.filter(e => e.type === 'AW').length - counts.AW;
  summary.innerHTML = policy === 'decentralized'
    ? `<span class="auction-proof">WMS announces only</span>
       <span>${counts.TN} tasks · ${counts.BD} bids · ${counts.AW} awards ·
       ${renewals} lease renewals · ${counts.TD} done</span>`
    : policy === 'BIOS_1.0.0'
    ? `<span class="auction-proof">BIOS peer auction</span>
       <span>${counts.TN} tasks · ${counts.BD} bids · ${counts.AW} awards ·
       ${renewals} lease renewals</span>`
    : `<span>${escapeHtml(policy)} task messages</span>
       <span>${counts.TN} announced · ${counts.AW} awards</span>`;

  if (!visibleEvents.length) {
    log.innerHTML = '<p class="muted">No auction messages yet.</p>';
    return;
  }

  const rows = visibleEvents.slice(-18).reverse().map(event => {
    const type = escapeHtml(event.type);
    const task = escapeHtml(event.task || '-');
    const source = escapeHtml(event.src);
    let detail = '';
    if (event.type === 'TN') detail = 'WMS -> all robots';
    if (event.type === 'BD') detail = `cost ${Number(event.cost).toFixed(1)}`;
    if (event.type === 'AW') {
      const winner = escapeHtml(event.winner || event.dst || event.src);
      detail = `winner ${winner} · cost ${Number(event.cost).toFixed(1)}`;
      if (event.u !== undefined) detail += ` · lease ${Number(event.u).toFixed(1)}s`;
    }
    if (event.type === 'TD') detail = 'completed';
    return `<div class="auction-row type-${type}">
      <span class="auction-time">${Number(event.t).toFixed(1)}s</span>
      <b>${type}</b><span>${source} · ${task}</span>
      <small>${detail}</small>
    </div>`;
  });
  log.innerHTML = rows.join('');
}

function renderSummary(s, meta) {
  const contacts = s.contacts_robot_robot + s.contacts_robot_human;
  const finished = s.completed_all;

  el('summary').innerHTML = `
    <dl>
      <dt>Tasks completed</dt>
      <dd class="${finished ? 'good' : ''}">${s.tasks_completed} / ${s.tasks_announced}</dd>
      <dt>${finished ? 'Makespan' : 'Ran for'}</dt>
      <dd>${s.makespan_s.toFixed(1)} s${finished ? '' : ' (timeout)'}</dd>
      <dt>Robot&ndash;robot contacts</dt>
      <dd class="${s.contacts_robot_robot ? 'bad' : 'good'}">${s.contacts_robot_robot}</dd>
      <dt>Robot&ndash;human contacts</dt>
      <dd class="${s.contacts_robot_human ? 'bad' : 'good'}">${s.contacts_robot_human}</dd>
      <dt>Worst separation</dt>
      <dd>${s.min_separation_m.toFixed(2)} m</dd>
      <dt>Deadlocks broken</dt>
      <dd>${s.deadlocks_detected}</dd>
      <dt>Give-way manoeuvres</dt>
      <dd>${s.retreats}</dd>
      <dt>Messages / robot / s</dt>
      <dd>${s.msgs_per_robot_s.toFixed(1)}</dd>
      <dt>Bytes / robot / s</dt>
      <dd>${s.bytes_per_robot_s.toFixed(0)}</dd>
      <dt>Planner CPU (mean / max)</dt>
      <dd>${s.plan_cpu_mean_ms.toFixed(2)} / ${s.plan_cpu_max_ms.toFixed(1)} ms</dd>
      <dt>Time in degraded mode</dt>
      <dd>${s.seconds_degraded.toFixed(0)} s</dd>
    </dl>
    <p class="caveat">
      ${contacts === 0
        ? `Zero contacts over ${(s.robot_hours * 1000).toFixed(1)} milli-robot-hours bounds
           the collision <i>rate</i>; it does not establish zero. Pool seeds with
           <code>run.py --seeds N</code> for an interval worth quoting.`
        : `${contacts} contact(s) recorded. A contact is a physical overlap in the
           ground-truth world, checked swept rather than at frame endpoints.`}
      ${finished ? '' : ' This run did not complete its task set, so its duration is a timeout and not a makespan &mdash; the two are not comparable.'}
    </p>`;
}

boot();
