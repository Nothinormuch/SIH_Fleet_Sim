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
  // The BIOS_4 model currently loaded, as {id, meta}. Null means BIOS_4 cannot run -
  // the server refuses it rather than silently running an untrained control.
  model: null,
  trainJob: null,
  trainTimer: null,
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
  el('policy').addEventListener('change', syncPolicyUI);
  el('trainBtn').addEventListener('click', startTraining);
  el('cancelBtn').addEventListener('click', cancelTraining);
  el('uploadBtn').addEventListener('click', () => el('modelFile').click());
  el('modelFile').addEventListener('change', uploadModel);
  syncPolicyUI();
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
  if (App.model) q.set('model', App.model.id);
  el('runBtn').disabled = true;
  App.playing = false;
  el('playBtn').textContent = 'Play';
  setStatus(`Simulating ${el('duration').value}s of ${el('scenario').value}…`, 'busy');

  try {
    const res = await fetch('/api/run?' + q.toString());
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);

    App.data = payload;
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

/* ------------------------------------------------------------------ BIOS_4
 *
 * Train, download, upload. The three things a learned policy needs that a
 * hand-written one does not.
 *
 * Training is a JOB, not a request: it runs for minutes and the browser polls it.
 * That shape is forced by the work, but it also buys the thing that matters on a
 * dashboard - you can watch the search and stop it when the curve flattens, instead
 * of staring at a spinner and guessing.
 */

function syncPolicyUI() {
  const isBios4 = el('policy').value === 'BIOS_4';
  el('bios4').hidden = !isBios4;
  // A run button that is enabled and then fails is worse than one that explains
  // itself: BIOS_4 without a model is refused by the server by design.
  el('runBtn').disabled = isBios4 && !App.model;
  if (isBios4 && !App.model) {
    setStatus('BIOS_4 needs a model — train one, or upload a .json.', 'busy');
  }
}

function setModel(id, meta, note) {
  App.model = { id, meta: meta || {} };
  const bits = [];
  if (meta && meta.fitness !== undefined) bits.push(`fitness ${meta.fitness}`);
  if (meta && meta.generations) bits.push(`${meta.generations} gens`);
  if (meta && meta.best_tasks !== undefined && meta.best_tasks !== null) {
    bits.push(`${meta.best_tasks} tasks in training`);
  }
  el('modelStatus').textContent =
    `${note} · ${id}${bits.length ? ' · ' + bits.join(' · ') : ''}`;
  el('modelStatus').className = 'status ok';
  syncPolicyUI();
}

async function startTraining() {
  const body = {
    scenario: el('scenario').value,
    robots: parseInt(el('robots').value, 10),
    population: parseInt(el('population').value, 10),
    generations: parseInt(el('generations').value, 10),
  };
  el('trainBtn').disabled = true;
  try {
    const res = await fetch('/api/train', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const started = await res.json();
    if (!res.ok) throw new Error(started.error || `HTTP ${res.status}`);
    App.trainJob = started.job;
    el('trainProgress').hidden = false;
    el('cancelBtn').hidden = false;
    el('trainStatus').textContent =
      `${started.params} parameters · ${started.episodes_per_generation} episodes per generation`;
    el('trainStatus').className = 'status busy';
    pollTraining();
  } catch (e) {
    el('trainStatus').textContent = 'Training failed to start: ' + e.message;
    el('trainStatus').className = 'status err';
    el('trainProgress').hidden = false;
    el('cancelBtn').hidden = true;
    el('trainBtn').disabled = false;
  }
}

function pollTraining() {
  clearTimeout(App.trainTimer);
  App.trainTimer = setTimeout(async () => {
    if (!App.trainJob) return;
    try {
      const res = await fetch(`/api/train/status?job=${App.trainJob}`);
      const st = await res.json();
      if (!res.ok) throw new Error(st.error || `HTTP ${res.status}`);
      renderTraining(st);
      if (st.state === 'running') return pollTraining();
      finishTraining(st);
    } catch (e) {
      el('trainStatus').textContent = 'Lost the training job: ' + e.message;
      el('trainStatus').className = 'status err';
      el('trainBtn').disabled = false;
    }
  }, 1000);
}

function renderTraining(st) {
  const done = st.history.length;
  el('trainBar').style.width = `${Math.round(100 * done / Math.max(1, st.generations))}%`;
  const last = st.history[done - 1];
  if (!last) return;
  // Surface a serial fallback loudly. It is ~12x slower and otherwise looks
  // identical to a machine that is merely busy.
  const serial = last.serial ? ' · SERIAL (no worker pool)' : '';
  el('trainStatus').textContent =
    `gen ${last.gen + 1}/${st.generations} · best ${last.best_so_far} · ` +
    `${last.best_tasks} tasks · ${last.elapsed_s}s${serial}`;
  el('trainStatus').className = 'status busy';
  drawFitness(st.history);
}

function drawFitness(history) {
  const c = el('trainChart');
  const g = c.getContext('2d');
  const w = c.width, h = c.height;
  g.clearRect(0, 0, w, h);
  if (history.length < 2) return;
  const ys = history.map(e => e.best_so_far);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  // A flat search is a real answer, and a chart that autoscales a flat line into a
  // dramatic wiggle is a lie about it.
  const span = (hi - lo) || 1;
  g.strokeStyle = '#5b8cff';
  g.lineWidth = 1.5;
  g.beginPath();
  ys.forEach((y, i) => {
    const px = (i / (ys.length - 1)) * (w - 2) + 1;
    const py = h - 2 - ((y - lo) / span) * (h - 4);
    i ? g.lineTo(px, py) : g.moveTo(px, py);
  });
  g.stroke();
}

async function finishTraining(st) {
  App.trainJob = null;
  el('trainBtn').disabled = false;
  // Nothing left to cancel. A button that is still offered after the job it would
  // have stopped has finished is a button that does nothing when pressed.
  el('cancelBtn').hidden = true;
  if (st.state === 'failed') {
    el('trainStatus').textContent = 'Training failed: ' + (st.error || 'unknown');
    el('trainStatus').className = 'status err';
    return;
  }
  const verb = st.state === 'cancelled' ? 'Cancelled' : 'Trained';
  el('trainStatus').textContent =
    `${verb} after ${st.history.length} generations · best fitness ${st.fitness}`;
  el('trainStatus').className = 'status ok';

  // Fetch it once: the same bytes get loaded for running AND handed to the user, so
  // what they download is provably the model the dashboard is about to run.
  try {
    const res = await fetch(`/api/train/model?job=${st.id}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const model = await res.json();
    setModel(st.model_id, model.meta, verb === 'Cancelled' ? 'cancelled run' : 'trained');
    downloadJson(model, `bios4-${st.id}.json`);
  } catch (e) {
    el('trainStatus').textContent += ` (download failed: ${e.message})`;
  }
}

function downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 1)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking immediately can cancel the download in some browsers; one tick is enough.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function cancelTraining() {
  if (!App.trainJob) return;
  el('cancelBtn').disabled = true;
  try {
    await fetch(`/api/train/cancel?job=${App.trainJob}`, { method: 'POST' });
    el('trainStatus').textContent += ' · stopping after this generation…';
  } finally {
    el('cancelBtn').disabled = false;
  }
}

async function uploadModel(ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  el('modelStatus').textContent = `Uploading ${file.name}…`;
  el('modelStatus').className = 'status busy';
  try {
    const text = await file.text();
    const res = await fetch('/api/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: text,
    });
    const body = await res.json();
    // The server's rejections are written to be read by a person - a model trained
    // against an older feature set is a normal accident - so show them verbatim
    // rather than replacing them with "upload failed".
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    setModel(body.model, body.meta, file.name);
  } catch (e) {
    el('modelStatus').textContent = e.message;
    el('modelStatus').className = 'status err';
    App.model = null;
    syncPolicyUI();
  } finally {
    ev.target.value = '';
  }
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
           manager_alive: f0.manager_alive, contacts: f0.contacts };
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
}

function updateManagerDot(frame) {
  const dot = el('mgrDot');
  const text = el('mgrText');
  // Whether this policy has a manager at all is the backend's call, not a string
  // match here: stop_and_wait and BIOS_1.0.0 are both manager-free by design, and a
  // red DOWN badge would misreport intent as failure. Older payloads lack the flag,
  // hence the fallback.
  const policy = App.data.meta.policy;
  const managed = App.data.meta.has_manager !== undefined
    ? App.data.meta.has_manager
    : (policy === 'central' || policy === 'hierarchical');
  if (!managed) {
    dot.className = 'dot';
    text.textContent = `no fleet manager · ${policy === 'stop_and_wait' ? 'baseline' : 'peer-to-peer by design'}`;
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
