import { DigitalTwin } from './digital-twin.js';

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
  twin: null,
  pipView: null,
  imgs: null,
  data: null,        // { map, meta, frames, summary }
  staticLayer: null,
  playing: false,
  simTime: 0,
  lastRaf: 0,
  speed: 1,
  auctionEvents: [],
  decisionEvents: [],
  // Camera angle & inspection mode
  cameraMode: 'overview', // 'overview' | 'follow' | 'pov'
  selectedRobotId: null,
  zoomLevel: 2.8,
  pipEnabled: false,
  pipPov: false,
  hudDetails: false,
  viewMode: '3d',
  presentationMode: false,
  showcase: [],
};

/* ------------------------------------------------------------------ boot */

async function boot() {
  App.view = new View(el('floor'));
  App.pipView = new View(el('pipCanvas'));
  App.twin = new DigitalTwin(el('twinCanvas'), id => {
    selectRobot(id);
    setCameraMode('follow');
  });
  App.imgs = await loadAssets();

  try {
    const r = await fetch('/api/scenarios');
    const { scenarios, showcase, policies, allocation_policies } = await r.json();
    App.showcase = showcase || [];
    fill(el('scenario'), scenarios, 'showcase_open_floor');
    fill(el('policy'), policies, 'BIOS_PIBT.6');
    fill(el('allocationPolicy'), allocation_policies, 'auction_bundle');
    renderScenarioGallery(App.showcase);
    updatePolicyProfile();
  } catch (e) {
    setStatus('Could not reach the server. Is backend/server.py running?', 'err');
  }

  // Simulation controls
  el('runBtn').addEventListener('click', run);
  el('policy').addEventListener('change', updatePolicyProfile);
  el('allocationPolicy').addEventListener('change', updatePolicyProfile);
  el('playBtn').addEventListener('click', togglePlay);
  el('speed').addEventListener('change', e => { App.speed = parseFloat(e.target.value); });
  el('scrub').addEventListener('input', e => {
    if (!App.data) return;
    App.playing = false;
    setPlaybackState(false);
    App.simTime = frameTime(parseInt(e.target.value, 10));
    draw();
  });

  // Camera Toolbar Controls
  el('camModeOverview').addEventListener('click', () => setCameraMode('overview'));
  el('camModeTactical').addEventListener('click', () => setCameraMode('tactical'));
  el('camModeFollow').addEventListener('click', () => setCameraMode('follow'));
  el('camModePov').addEventListener('click', () => setCameraMode('pov'));
  el('camTargetSelect').addEventListener('change', e => selectRobot(e.target.value));
  el('camZoomIn').addEventListener('click', () => adjustZoom(0.4));
  el('camZoomOut').addEventListener('click', () => adjustZoom(-0.4));

  // PiP Viewfinder Controls
  el('camPipToggle').addEventListener('click', togglePip);
  el('hudDetailsToggle').addEventListener('click', toggleHudDetails);
  el('pipCloseBtn').addEventListener('click', togglePip);
  el('pipPovToggle').addEventListener('click', togglePipPov);
  el('pipFocusMainBtn').addEventListener('click', () => {
    if (App.selectedRobotId) setCameraMode('follow');
  });

  el('view3dBtn').addEventListener('click', () => setViewMode('3d'));
  el('view2dBtn').addEventListener('click', () => setViewMode('2d'));
  el('presentationBtn').addEventListener('click', togglePresentationMode);
  el('exitJuryBtn').addEventListener('click', togglePresentationMode);
  el('fullscreenBtn').addEventListener('click', toggleFullscreen);

  // A live camera consumes most of a phone-sized stage. Start it closed below the
  // tablet breakpoint; the operator can still open it explicitly from the toolbar.
  if (!window.matchMedia('(min-width: 1081px)').matches) App.pipEnabled = false;
  syncOverlayState();

  // Canvas Click to Focus Robot
  el('floor').addEventListener('click', onCanvasClick);

  // Keyboard Navigation
  window.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.code === 'Space') {
      e.preventDefault();
      togglePlay();
    } else if (e.code === 'KeyV') {
      cycleCameraMode();
    } else if (e.code === 'KeyC') {
      cycleTargetRobot();
    } else if (e.code === 'KeyZ') {
      adjustZoom(-0.4);
    } else if (e.code === 'KeyX') {
      adjustZoom(0.4);
    }
  });

  window.addEventListener('resize', () => {
    App.twin.resize();
    if (!App.data) return;
    App.view.resize(App.data.map, App.data.meta.cell_m);
    App.pipView.resize(App.data.map, App.data.meta.cell_m);
    App.staticLayer = buildStaticLayer(App.view, App.data.map, App.imgs);
    Hud.resize(App.data.map);
    draw();
  });

  requestAnimationFrame(tick);
  run();
}

function updatePolicyProfile() {
  const policy = el('policy').value;
  const allocation = el('allocationPolicy').value;
  const isV6 = policy === 'BIOS_PIBT.6';
  el('bios6Intelligence')?.classList.toggle('is-v6', isV6);
  const proof = el('predictiveProof');
  if (proof) proof.hidden = !isV6;
  const profile = el('launchProfile');
  if (profile) {
    profile.textContent = isV6
      ? `BIOS 6.0 · ${allocation} · predictive edge`
      : `${policy.replaceAll('_', ' ')} · ${allocation} · energy gate`;
  }
  const mode = el('collectiveMode');
  if (mode) mode.textContent = isV6 ? 'PREDICTIVE EDGE' : 'V6 NOT SELECTED';
}

function renderScenarioGallery(showcase) {
  const gallery = el('scenarioGallery');
  const accents = {cyan: '#35c6f4', amber: '#f5b843', violet: '#b78cff', rose: '#ff6577', lime: '#a3e635'};
  gallery.innerHTML = showcase.map((item, index) => `
    <button class="scenario-card ${index === 0 ? 'active' : ''}" data-scenario="${escapeHtml(item.id)}"
      style="--card-accent:${accents[item.accent] || accents.cyan}">
      <span class="scenario-index">0${index + 1}</span>
      <span class="scenario-copy"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.eyebrow)}</small></span>
      <span class="scenario-meta">${item.robots} AMRs${item.humans ? ' · ' + item.humans + ' people' : ''}</span>
    </button>`).join('');
  gallery.querySelectorAll('.scenario-card').forEach(card => {
    card.addEventListener('click', () => selectScenarioProfile(card.dataset.scenario));
  });
  if (showcase.length) selectScenarioProfile(showcase[0].id, false);
}

function selectScenarioProfile(id, announce = true) {
  const profile = App.showcase.find(item => item.id === id);
  if (!profile) return;
  el('scenario').value = profile.id;
  el('robots').value = profile.robots;
  el('seed').value = profile.seed;
  el('duration').value = profile.duration;
  el('activeScenarioTitle').textContent = profile.title;
  el('activeScenarioEyebrow').textContent = profile.eyebrow.toUpperCase();
  el('activeScenarioDescription').textContent = profile.description;
  document.querySelectorAll('.scenario-card').forEach(card => {
    card.classList.toggle('active', card.dataset.scenario === id);
  });
  if (announce) setStatus(`${profile.title} selected · energy-aware auction is active.`);
}

function fill(select, values, preferred) {
  const labels = {
    'BIOS_PIBT.6': 'BIOS 6.0 · Predictive',
    'BIOS_PIBT.5': 'BIOS 5.0 · Energy-aware',
    'BIOS_PIBT.3': 'BIOS 3.0 · Priority traffic',
    'stop_and_wait': 'Stop-and-wait · basic',
    'stop_and_wait_competition': 'Stop-and-wait · competition',
    'central': 'Central route baseline',
    'prioritized_space_time_astar': 'Prioritized space-time A* · central',
    'hierarchical': 'Hierarchical baseline',
    'decentralized': 'Peer intent baseline',
  };
  select.innerHTML = '';
  for (const v of values) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = labels[v] || v.replace(/_/g, ' ');
    if (v === preferred) o.selected = true;
    select.appendChild(o);
  }
}

/* ------------------------------------------------------------------ camera modes */

function setCameraMode(mode, redraw = true) {
  App.cameraMode = mode;
  el('camModeOverview').classList.toggle('active', mode === 'overview');
  el('camModeTactical').classList.toggle('active', mode === 'tactical');
  el('camModeFollow').classList.toggle('active', mode === 'follow');
  el('camModePov').classList.toggle('active', mode === 'pov');
  App.twin.setCameraMode(mode);

  if (mode !== 'overview' && !App.selectedRobotId && App.data && App.data.frames.length) {
    const firstRobot = App.data.frames[0].robots[0];
    if (firstRobot) selectRobot(firstRobot.id);
  }
  if (redraw) draw();
}

function cycleCameraMode() {
  const modes = ['overview', 'tactical', 'follow', 'pov'];
  const nextIdx = (modes.indexOf(App.cameraMode) + 1) % modes.length;
  setCameraMode(modes[nextIdx]);
}

function selectRobot(id, redraw = true) {
  App.selectedRobotId = id || null;
  App.twin.setSelected(App.selectedRobotId);
  const sel = el('camTargetSelect');
  if (sel) sel.value = id || '';

  if (id && el('pipContainer').classList.contains('hidden') && App.pipEnabled) {
    el('pipContainer').classList.remove('hidden');
    el('camPipToggle').classList.add('active');
    syncOverlayState();
  }
  if (redraw) draw();
}

function cycleTargetRobot() {
  if (!App.data || !App.data.frames.length) return;
  const robots = App.data.frames[0].robots;
  if (!robots.length) return;

  if (!App.selectedRobotId) {
    selectRobot(robots[0].id);
    if (App.cameraMode === 'overview') setCameraMode('follow');
    return;
  }
  const curIdx = robots.findIndex(r => r.id === App.selectedRobotId);
  const nextIdx = (curIdx + 1) % robots.length;
  selectRobot(robots[nextIdx].id);
}

function adjustZoom(delta) {
  App.zoomLevel = Math.max(1.4, Math.min(6.0, parseFloat((App.zoomLevel + delta).toFixed(1))));
  el('camZoomVal').textContent = `${App.zoomLevel.toFixed(1)}×`;
  if (App.viewMode === '3d') App.twin.zoom(delta);
  draw();
}

function setViewMode(mode) {
  App.viewMode = mode;
  const is3d = mode === '3d';
  document.body.classList.toggle('view-2d', !is3d);
  el('twinCanvas').classList.toggle('is-hidden', !is3d);
  el('floor').classList.toggle('is-hidden', is3d);
  el('view3dBtn').classList.toggle('active', is3d);
  el('view2dBtn').classList.toggle('active', !is3d);
  el('view3dBtn').setAttribute('aria-pressed', String(is3d));
  el('view2dBtn').setAttribute('aria-pressed', String(!is3d));
  if (is3d) App.twin.resize();
  if (App.data) draw();
}

function toggleFullscreen() {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
  else document.exitFullscreen?.();
}

function togglePresentationMode() {
  App.presentationMode = !App.presentationMode;
  document.body.classList.toggle('jury-mode', App.presentationMode);
  el('juryOverlay').setAttribute('aria-hidden', String(!App.presentationMode));
  el('presentationBtn').classList.toggle('active', App.presentationMode);
  if (App.presentationMode) {
    setViewMode('3d');
    if (!App.playing && App.data) togglePlay();
    if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  } else if (document.fullscreenElement && document.exitFullscreen) {
    document.exitFullscreen().catch(() => {});
  }
  setTimeout(() => App.twin.resize(), 80);
}

function togglePip() {
  if (!App.pipEnabled && App.hudDetails) setHudDetails(false);
  App.pipEnabled = !App.pipEnabled;
  syncOverlayState();
  if (App.pipEnabled) draw();
}

function toggleHudDetails() {
  setHudDetails(!App.hudDetails);
}

function setHudDetails(enabled) {
  App.hudDetails = enabled;
  // Tactical cards and PiP compete for the same safe canvas region. Make the modes
  // mutually exclusive instead of letting one silently cover the other.
  if (enabled) App.pipEnabled = false;
  syncOverlayState();
}

function syncOverlayState() {
  const stage = document.querySelector('.twin-stage');
  const hud = el('hud');
  const pip = el('pipContainer');
  const pipButton = el('camPipToggle');
  const hudButton = el('hudDetailsToggle');
  if (stage) {
    stage.classList.toggle('pip-open', App.pipEnabled);
    stage.classList.toggle('hud-details-open', App.hudDetails);
  }
  if (hud) hud.classList.toggle('hud-details', App.hudDetails);
  if (pip) pip.classList.toggle('hidden', !App.pipEnabled);
  if (pipButton) {
    pipButton.classList.toggle('active', App.pipEnabled);
    pipButton.setAttribute('aria-pressed', String(App.pipEnabled));
  }
  if (hudButton) {
    hudButton.classList.toggle('active', App.hudDetails);
    hudButton.setAttribute('aria-expanded', String(App.hudDetails));
    hudButton.textContent = App.hudDetails ? 'Core HUD' : 'HUD details';
  }
}

function togglePipPov() {
  App.pipPov = !App.pipPov;
  el('pipPovToggle').classList.toggle('active', App.pipPov);
  draw();
}

function updateCamTargetOptions(robots) {
  const sel = el('camTargetSelect');
  if (!sel) return;
  sel.innerHTML = '<option value="">Overview (None)</option>';
  for (const r of robots) {
    const opt = document.createElement('option');
    opt.value = r.id;
    opt.textContent = `🎯 ${r.id}`;
    sel.appendChild(opt);
  }
  if (App.selectedRobotId && robots.some(r => r.id === App.selectedRobotId)) {
    sel.value = App.selectedRobotId;
  }
}

function onCanvasClick(e) {
  if (!App.data || !App.data.frames.length) return;
  const rect = el('floor').getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const [wx, wy] = App.view.screenToWorld(sx, sy);

  const [f0, f1, u] = bracket(App.simTime);
  const frame = interpolate(f0, f1, u);
  const clickDistM = Math.max(0.9, (App.data.meta.robot_diameter_m || 0.8) * 1.5);

  let closest = null, minD = Infinity;
  for (const r of frame.robots) {
    const d = Math.hypot(r.x - wx, r.y - wy);
    if (d < clickDistM && d < minD) {
      minD = d;
      closest = r.id;
    }
  }

  if (closest) {
    selectRobot(closest);
    if (App.cameraMode === 'overview') setCameraMode('follow');
  }
}

/* ------------------------------------------------------------------ running */

async function run() {
  const request = {
    scenario: el('scenario').value,
    policy: el('policy').value,
    allocation_policy: el('allocationPolicy').value,
    robots: Number(el('robots').value),
    seed: Number(el('seed').value),
    duration: Number(el('duration').value),
  };
  el('runBtn').disabled = true;
  App.playing = false;
  setPlaybackState(false);
  setStatus(`Simulating ${el('duration').value}s of ${el('scenario').value}…`, 'busy');

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(request),
    });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || `HTTP ${res.status}`);

    App.data = payload;
    App.auctionEvents = payload.frames.flatMap(f => f.auction_events || []);
    const seenDecisions = new Set();
    App.decisionEvents = payload.frames.flatMap(frame =>
      (frame.fleet || []).map(robot => robot.decision).filter(Boolean)
    ).filter(decision => {
      const key = `${decision.robot}|${decision.t}|${decision.code}`;
      if (seenDecisions.has(key)) return false;
      seenDecisions.add(key);
      return true;
    });
    App.simTime = 0;
    const humanProof = el('humanProof');
    humanProof.hidden = !payload.meta.humans;
    el('humanProofText').textContent = `${payload.meta.humans} mapped worker${payload.meta.humans === 1 ? '' : 's'}`;

    if (payload.frames.length && payload.frames[0].robots.length) {
      if (!App.selectedRobotId || !payload.frames[0].robots.some(r => r.id === App.selectedRobotId)) {
        App.selectedRobotId = payload.frames[0].robots[0].id;
      }
      updateCamTargetOptions(payload.frames[0].robots);
    }

    App.view.resize(payload.map, payload.meta.cell_m);
    App.pipView.resize(payload.map, payload.meta.cell_m);
    App.staticLayer = buildStaticLayer(App.view, payload.map, App.imgs);
    App.twin.load(payload);
    App.twin.setSelected(App.selectedRobotId);

    const n = payload.frames.length;
    el('scrub').max = Math.max(0, n - 1);
    el('scrub').value = 0;
    el('clockEnd').textContent = (n ? payload.frames[n - 1].t : 0).toFixed(1);

    renderSummary(payload.summary, payload.meta);
    renderCollectiveIntelligence(payload.frames[0]);
    setStatus(`${n} frames · ${payload.meta.robots} AMRs · seed ${payload.meta.seed} · energy gate active`);

    // HUD is re-inited on every run; init() disposes any previous instance so
    // replaying / re-running never stacks overlays.
    Hud.init(App.view, App.imgs, payload);

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

function setPlaybackState(playing) {
  const button = el('playBtn');
  const icon = button?.querySelector('.play-icon');
  if (icon) icon.textContent = playing ? 'Ⅱ' : '▶';
  if (button) {
    const action = playing ? 'Pause' : 'Play';
    button.title = action;
    button.setAttribute('aria-label', `${action} simulation`);
  }
  const label = el('playStateLabel');
  if (label) label.textContent = playing ? 'Pause' : 'Play';
}

function togglePlay() {
  if (!App.data || !App.data.frames.length) return;
  App.playing = !App.playing;
  setPlaybackState(App.playing);
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
    setPlaybackState(false);
  }
  draw();
}

/* Which two frames bracket the current sim time, and how far between them are we? */
function bracket(t) {
  const f = App.data.frames;
  if (f.length < 2) return [f[0], f[0], 0, 0];
  if (t <= f[0].t) return [f[0], f[1], 0, 0];
  if (t >= f[f.length - 1].t) {
    return [f[f.length - 2], f[f.length - 1], 1, f.length - 2];
  }
  // Normal telemetry is 10 Hz, but a run may append a completion frame between two
  // scheduled samples. Binary search the recorded timestamps instead of assuming a
  // perfectly uniform index-to-time mapping.
  let lo = 0, hi = f.length - 1;
  while (lo + 1 < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (f[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const span = Math.max(1e-9, f[hi].t - f[lo].t);
  const u = Math.max(0, Math.min(1, (t - f[lo].t) / span));
  return [f[lo], f[hi], u, lo];
}

const lerp = (a, b, u) => a + (b - a) * u;

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
      carry: u >= 0.5 ? b.carry : a.carry,
    };
  });
  const hById = {};
  for (const h of (f1.humans || [])) hById[h.id] = h;
  const humans = (f0.humans || []).map(a => {
    const b = hById[a.id] || a;
    const aHeading = Number.isFinite(a.th) ? a.th : 0;
    const bHeading = Number.isFinite(b.th) ? b.th : aHeading;
    return {
      id: a.id,
      x: lerp(a.x, b.x, u),
      y: lerp(a.y, b.y, u),
      th: lerpAngle(aHeading, bHeading, u),
      paused: u >= .5 ? Boolean(b.paused) : Boolean(a.paused),
      yield_ticks: u >= .5 ? Number(b.yield_ticks || 0) : Number(a.yield_ticks || 0),
    };
  });
  return { t: lerp(f0.t, f1.t, u), robots, humans,
           obstacles: u >= 0.5 ? (f1.obstacles || []) : (f0.obstacles || []),
           fleet: u >= 0.5 ? (f1.fleet || f0.fleet) : f0.fleet,
           tasks_completed: u >= 0.5
             ? Number(f1.tasks_completed || 0) : Number(f0.tasks_completed || 0),
           manager_alive: f0.manager_alive, contacts: f0.contacts,
           auction_events: f0.auction_events || [] };
}

/* ------------------------------------------------------------------ drawing */

function draw() {
  if (!App.data || !App.data.frames.length) return;
  const [f0, f1, u, idx] = bracket(App.simTime);
  const frame = interpolate(f0, f1, u);

  // Determine active camera target robot position
  const selectedRobot = frame.robots.find(r => r.id === App.selectedRobotId) || frame.robots[0];

  if (App.viewMode === '3d') {
    App.twin.update(frame, App.selectedRobotId, App.cameraMode, frame.t);
  } else {
    // The original evidence-focused 2D diagnostic view remains available as a fallback.
    App.view.setCamera(App.cameraMode === 'tactical' ? 'overview' : App.cameraMode,
      App.selectedRobotId, App.zoomLevel);
    App.view.updateCameraTransform(selectedRobot);
    const { ctx } = App.view;
    App.view.clear();
    ctx.save();
    if (App.view.camRotation !== 0) {
      ctx.translate(App.view.cssW / 2, App.view.cssH / 2);
      ctx.rotate(App.view.camRotation);
      ctx.translate(-App.view.cssW / 2, -App.view.cssH / 2);
    }
    if ((App.cameraMode === 'overview' || App.cameraMode === 'tactical') && App.staticLayer) {
      ctx.drawImage(App.staticLayer, 0, 0, App.view.cssW, App.view.cssH);
    } else {
      renderStaticFloor(ctx, App.view, App.data.map, App.imgs);
    }
    drawNetwork(ctx, App.view, frame, App.imgs, frame.t);
    const diameterCells = App.data.meta.robot_diameter_m / App.data.meta.cell_m;
    drawFleet(ctx, App.view, frame, App.imgs, {
      labels: App.view.cell >= 20,
      robotSizeCells: Math.max(0.55, diameterCells),
      selectedRobotId: App.selectedRobotId,
    });
    ctx.restore();
  }

  // Render PiP Close-Up Viewfinder
  renderPiP(frame, selectedRobot);

  el('scrub').value = Math.min(Number(el('scrub').max), idx + (u >= .5 ? 1 : 0));
  el('clockNow').textContent = frame.t.toFixed(1);
  updateManagerDot(frame);
  renderFleetPanel(frame);
  renderAuctionPanel(frame);
  updateSummaryProgress(frame);
  renderRobotInspector(frame);
  renderCollectiveIntelligence(frame);
  updateEventSpotlight(frame);
  if (App.presentationMode) updatePresentation(frame);
  Hud.render(frame, App.data.summary, App.data.meta, frame.t);
}

function renderPiP(frame, activeRobot) {
  const container = el('pipContainer');
  if (!container || container.classList.contains('hidden') || !App.pipEnabled) return;

  const target = activeRobot || frame.robots[0];
  if (!target) return;

  const fInfo = (frame.fleet || []).find(f => f.id === target.id) || {};
  el('pipRobotLabel').textContent = `${target.id} · LIVE CAM`;

  // Size PiP canvas properly
  App.pipView.resize(App.data.map, App.data.meta.cell_m);
  App.pipView.setCamera(App.pipPov ? 'pov' : 'follow', target.id, 3.2);
  App.pipView.updateCameraTransform(target);

  const ctx = App.pipView.ctx;
  App.pipView.clear();

  ctx.save();
  if (App.pipView.camRotation !== 0) {
    ctx.translate(App.pipView.cssW / 2, App.pipView.cssH / 2);
    ctx.rotate(App.pipView.camRotation);
    ctx.translate(-App.pipView.cssW / 2, -App.pipView.cssH / 2);
  }

  renderStaticFloor(ctx, App.pipView, App.data.map, App.imgs);
  drawNetwork(ctx, App.pipView, frame, App.imgs, frame.t);
  const diameterCells = App.data.meta.robot_diameter_m / App.data.meta.cell_m;
  drawFleet(ctx, App.pipView, frame, App.imgs, {
    labels: true,
    robotSizeCells: Math.max(0.65, diameterCells),
    selectedRobotId: target.id,
  });

  ctx.restore();

  // Update live HUD metrics
  const deg = ((target.th * 180 / Math.PI) % 360 + 360) % 360;
  const dirs = ['E', 'NE', 'N', 'NW', 'W', 'SW', 'S', 'SE'];
  const dirIdx = Math.round(deg / 45) % 8;
  const dir = dirs[dirIdx];

  const spd = fInfo.state === 'idle' || fInfo.state === 'charging' || fInfo.state === 'blocked' ? '0.00' : '0.85';
  const batt = Math.round((target.batt || 0) * 100);

  el('pipSpeed').textContent = `${spd} m/s`;
  el('pipHeading').textContent = `${deg.toFixed(0)}° ${dir}`;
  el('pipBatt').textContent = `${batt}%`;
  el('pipState').textContent = (fInfo.state || 'IDLE').toUpperCase();
}

function renderRobotInspector(frame) {
  const panel = el('robotInspector');
  const robot = frame.robots.find(item => item.id === App.selectedRobotId);
  if (!robot) return;
  const info = (frame.fleet || []).find(item => item.id === robot.id) || {};
  const battery = Math.max(0, Math.min(100, Math.round((Number(robot.batt) || 0) * 100)));
  const reserve = Math.round((App.data.meta.energy_reserve_frac || .15) * 100);
  const cargo = info.cargo_type
    ? `${String(info.cargo_type).toUpperCase()} · ${Number(info.cargo_weight || 0).toFixed(0)} kg`
    : 'WAITING FOR TASK';
  const task = info.task || 'UNASSIGNED';
  const network = App.data.meta.has_manager && info.mode === 'DEGRADED_P2P'
    ? 'DEGRADED P2P' : `P2P · ${(info.peers || []).length} PEERS`;
  const deadline = info.deadline == null ? 'NO HARD LIMIT' : `${Number(info.deadline).toFixed(0)} s`;
  panel.innerHTML = `
    <div class="inspector-head">
      <div><small>SELECTED EDGE AGENT</small><strong>${escapeHtml(robot.id)}</strong></div>
      <span class="inspector-state">${escapeHtml(String(info.state || 'idle').replace(/_/g, ' '))}</span>
    </div>
    <div class="inspector-grid">
      <div><span>Battery</span><b>${battery}%</b><div class="battery-track"><i style="width:${battery}%"></i></div></div>
      <div><span>Reserve floor</span><b>≥ ${reserve}%</b></div>
      <div><span>Task</span><b>${escapeHtml(task)}</b></div>
      <div><span>Cargo</span><b>${escapeHtml(cargo)}</b></div>
      <div><span>Network</span><b>${escapeHtml(network)}</b></div>
      <div><span>Deadline</span><b>${escapeHtml(deadline)}</b></div>
    </div>
    <div class="reserve-note"><b>Energy acceptance active.</b> The robot may bid only when task + charger-return reserve remains feasible.</div>`;
}

function decisionDetail(decision) {
  const details = decision?.details || {};
  if (decision.code === 'TASK_ACCEPTED') {
    const reserve = Number(details.projected_reserve_pct);
    return Number.isFinite(reserve) ? `Projected reserve ${reserve.toFixed(1)}%` : 'Battery and deadline accepted';
  }
  if (decision.code === 'PREDICTIVE_REROUTE') {
    return `${Number(details.direct_cells || 0)} → ${Number(details.selected_cells || 0)} cells · risk avoided ${Number(details.avoided_risk || 0).toFixed(1)}`;
  }
  if (decision.code === 'CONGESTION_REROUTE') {
    return `Learned delay avoided ${Number(details.avoided_delay_cells || 0).toFixed(1)} equivalent cells`;
  }
  if (decision.code === 'CHARGER_SELECTION') {
    return `Selected dock ${(details.selected_dock || []).join(', ')}`;
  }
  if (decision.code === 'TASK_COMPLETED') {
    return `${Number(details.elapsed_s || 0).toFixed(1)} s · battery ${Number(details.battery_pct || 0).toFixed(1)}%`;
  }
  if (decision.code === 'IDLE_VACATE') {
    return `Clearing for ${(details.requesting_robots || []).join(', ') || 'active traffic'}`;
  }
  return '';
}

function renderCollectiveIntelligence(frame) {
  const metrics = el('collectiveMetrics');
  const stream = el('thoughtStream');
  if (!metrics || !stream || !App.data) return;
  const isV6 = App.data.meta.policy === 'BIOS_PIBT.6';
  el('bios6Intelligence')?.classList.toggle('is-v6', isV6);
  el('collectiveMode').textContent = isV6 ? 'PREDICTIVE EDGE' : 'V6 NOT SELECTED';
  if (!isV6) {
    metrics.innerHTML = '<p class="muted">Select BIOS_PIBT.6 to activate predictive telemetry.</p>';
    stream.innerHTML = '<p class="muted">This policy does not publish BIOS 6 decision reasons.</p>';
    return;
  }

  const s = App.data.summary;
  const suppressed = Number(s.heartbeat_messages_suppressed || 0)
    + Number(s.intent_messages_suppressed || 0)
    + Number(s.lease_renewals_suppressed || 0)
    + Number(s.bid_rebroadcasts_suppressed || 0);
  metrics.innerHTML = `
    <div class="metric"><span>Forecasts observed</span><b>${Number(s.predictive_hazards_seen || 0)}</b></div>
    <div class="metric"><span>Predictive reroutes</span><b>${Number(s.predictive_reroutes || 0)}</b></div>
    <div class="metric"><span>Packets suppressed</span><b class="good">${suppressed.toLocaleString()}</b></div>
    <div class="metric"><span>Decision events</span><b>${Number(s.decision_events || 0)}</b></div>`;

  const visible = App.decisionEvents
    .filter(decision => decision.t <= frame.t + 1e-6)
    .filter(decision => !App.selectedRobotId || decision.robot === App.selectedRobotId);
  const fallback = App.decisionEvents.filter(decision => decision.t <= frame.t + 1e-6);
  const decisions = (visible.length ? visible : fallback).slice(-6).reverse();
  if (!decisions.length) {
    stream.innerHTML = '<p class="muted">Waiting for the first locally recorded decision.</p>';
    return;
  }
  stream.innerHTML = decisions.map(decision => `
    <article class="thought-row">
      <header><b>${escapeHtml(decision.robot)} · ${escapeHtml(decision.code.replaceAll('_', ' '))}</b><time>${Number(decision.t).toFixed(1)}s</time></header>
      <p>${escapeHtml(decision.summary)}</p>
      ${decisionDetail(decision) ? `<small>${escapeHtml(decisionDetail(decision))}</small>` : ''}
    </article>`).join('');
}

function eventDescription(event) {
  if (!event) return {tag: 'LIVE', text: 'Robots are coordinating from local state and peer intent.'};
  const task = event.task || 'task';
  if (event.type === 'TN') return {tag: 'ANNOUNCE', text: `${task} broadcast by WMS; robots now evaluate their own eligibility.`};
  if (event.type === 'BD') return {tag: 'BID', text: `${event.src} submitted a battery-feasible bid for ${task}.`};
  if (event.type === 'AW') return {tag: 'AWARD', text: `${event.winner || event.dst || event.src} won ${task}; the WMS did not choose the winner.`};
  if (event.type === 'TD') return {tag: 'COMPLETE', text: `${event.src} completed ${task}; completion is now shared with peers.`};
  return {tag: event.type || 'EVENT', text: `${event.src || 'Fleet'} updated ${task}.`};
}

function updateEventSpotlight(frame) {
  const latest = App.auctionEvents.filter(event => event.t <= frame.t + 1e-6).at(-1);
  const description = eventDescription(latest);
  el('eventSpotlight').innerHTML = `<span>${escapeHtml(description.tag)}</span><strong>${escapeHtml(description.text)}</strong>`;
}

function updatePresentation(frame) {
  const latest = App.auctionEvents.filter(event => event.t <= frame.t + 1e-6).at(-1);
  const description = eventDescription(latest);
  const fleet = frame.fleet || [];
  const active = fleet.find(item => item.task && !item.failed);
  if (active && active.id !== App.selectedRobotId) selectRobot(active.id, false);

  const phase = frame.t % 56;
  if (phase < 9) {
    setCameraMode('overview', false);
    el('juryHeadline').textContent = 'One warehouse. No central traffic brain.';
    el('juryNarration').textContent = 'The WMS announces work; every eligible AMR evaluates the task locally.';
  } else if (phase < 20) {
    setCameraMode('tactical', false);
    el('juryHeadline').textContent = description.tag === 'AWARD' ? 'The best eligible robot wins.' : 'Every bid is energy-feasible.';
    el('juryNarration').textContent = description.text;
  } else if (phase < 39) {
    setCameraMode('follow', false);
    const thought = active?.decision;
    el('juryHeadline').textContent = thought
      ? `${active.id} is explaining its own decision.`
      : 'Follow the decision into motion.';
    el('juryNarration').textContent = thought?.summary
      || 'The selected AMR publishes intent, reserves upcoming cells and retains charger-return reserve.';
  } else if (phase < 47 && (App.data.meta.humans || 0) > 0) {
    setCameraMode('pov', false);
    el('juryHeadline').textContent = 'A human does not broadcast intent.';
    el('juryNarration').textContent = 'Local perception—not a network message—must trigger the robot response.';
  } else {
    setCameraMode('tactical', false);
    el('juryHeadline').textContent = 'Leases expire. The fleet recovers.';
    el('juryNarration').textContent = 'Temporary reservations prevent stale claims from permanently blocking the warehouse.';
  }
}

function updateManagerDot(frame) {
  const dot = el('mgrDot');
  const text = el('mgrText');
  const routePolicy = App.data.meta.policy;
  const allocation = App.data.meta.allocation_policy;
  if (allocation === 'auction') {
    dot.className = 'dot ' + (frame.manager_alive ? 'up' : 'p2p');
    text.textContent = frame.manager_alive
      ? 'peer auction · route manager reachable'
      : 'WMS injector · peer auction';
    return;
  }
  if (allocation === 'hungarian') {
    dot.className = 'dot ' + (frame.manager_alive ? 'up' : 'down');
    text.textContent = frame.manager_alive
      ? 'Hungarian task allocator reachable'
      : 'Hungarian task allocator DOWN';
    return;
  }
  if (routePolicy === 'stop_and_wait'
      || routePolicy === 'stop_and_wait_competition'
      || routePolicy === 'BIOS_1.0.0') {
    dot.className = 'dot';
    text.textContent = routePolicy === 'BIOS_1.0.0'
      ? 'no fleet manager · peer traffic'
      : `no fleet manager · ${routePolicy === 'stop_and_wait_competition'
        ? 'competition stop-and-wait' : 'basic stop-and-wait'}`;
    return;
  }
  if (routePolicy === 'BIOS_PIBT.1' || routePolicy === 'BIOS_PIBT.2'
      || routePolicy === 'BIOS_PIBT.3' || routePolicy === 'BIOS_PIBT.5'
      || routePolicy === 'BIOS_PIBT.6'
      || routePolicy === 'BIOS_1.0.0') {
    dot.className = 'dot up';
    text.textContent = 'edge-only peer coordination · no manager';
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
    const batt = Math.max(0, Math.min(100, Math.round((Number(r.batt) || 0) * 100)));
    const battCls = batt < 15 ? 'crit' : (batt < 35 ? 'low' : '');
    const waiting = f.blocked_on
      ? (f.blocked_on === 'gate' ? 'awaiting block' : 'waiting on ' + f.blocked_on)
      : (f.task ? 'task ' + f.task : 'unassigned');
    const pk = f.priority_key;
    const priority = pk
      ? ` · P[e${pk[0]} x${pk[1]} w${pk[2]} a${pk[3]} l${pk[4]}]`
      : '';
    const isFocused = r.id === App.selectedRobotId;
    const state = String(f.state || 'idle');
    const stateClass = /^[a-z0-9_-]+$/i.test(state) ? state : 'idle';

    return `
      <div class="robot ${isFocused ? 'active-focus' : ''}" style="border-left-color:${colour}" data-rid="${r.id}">
        <span class="swatch" style="background:${colour}"></span>
        <div>
          <div class="rid">
            <span>${r.id}</span>
            ${isFocused ? '<span class="cam-indicator">CAM</span>' : ''}
          </div>
          <div class="meta">${waiting}${priority}</div>
          <div class="batt"><i class="${battCls}" style="width:${batt}%"></i></div>
        </div>
        <div class="right">
          <span class="state ${stateClass}">${escapeHtml(state.replace(/_/g, ' '))}</span>
          <div class="meta" style="margin-top:5px">${Number(f.done) || 0} done</div>
        </div>
      </div>`;
  });
  el('fleet').innerHTML = rows.join('');

  // Attach click listener to fleet cards for instant camera focus
  const cardEls = el('fleet').querySelectorAll('.robot');
  cardEls.forEach(card => {
    card.addEventListener('click', () => {
      const rid = card.getAttribute('data-rid');
      if (rid) {
        selectRobot(rid);
        if (App.cameraMode === 'overview') setCameraMode('follow');
      }
    });
  });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function uniqueAuctionEvents(events) {
  const seen = new Set();
  return events.filter(event => {
    let key;
    if (event.type === 'TN') key = ['TN', event.task, event.e ?? 0].join('|');
    else if (event.type === 'BD') key = ['BD', event.task, event.e ?? 0, event.src].join('|');
    else if (event.type === 'AW') key = ['AW', event.task, event.e ?? 0,
      event.winner || event.dst || event.src].join('|');
    else if (event.type === 'TD') key = ['TD', event.task, event.src].join('|');
    else return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderAuctionPanel(frame) {
  const summary = el('auctionSummary');
  const log = el('auctionLog');
  if (!summary || !log) return;

  const allocation = App.data.meta.allocation_policy;
  const events = App.auctionEvents.filter(e => e.t <= frame.t + 1e-6);
  const visibleEvents = uniqueAuctionEvents(events);
  const counts = { TN: 0, BD: 0, AW: 0, TD: 0 };
  for (const event of visibleEvents) {
    if (counts[event.type] !== undefined) counts[event.type]++;
  }
  const renewals = events.filter(e => e.type === 'AW').length - counts.AW;
  summary.innerHTML = allocation === 'auction'
    ? `<span class="auction-proof">WMS announces only</span>
       <span>${counts.TN} tasks · ${counts.BD} bids · ${counts.AW} awards ·
       ${renewals} lease renewals · ${counts.TD} done</span>`
    : allocation === 'hungarian'
    ? `<span class="auction-proof">WMS -> Hungarian manager</span>
       <span>${counts.TN} announced · ${counts.AW} assignments · ${counts.TD} done</span>`
    : `<span>pre-assigned workload</span>
       <span>${counts.TD} done</span>`;

  if (!visibleEvents.length) {
    log.innerHTML = '<p class="muted">No task-allocation messages yet.</p>';
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
  const contacts = s.contacts_robot_robot + s.contacts_robot_human
                 + s.contacts_robot_rack;
  const finished = s.completed_all;
  const remaining = Math.max(0, Number(s.tasks_announced || 0) - Number(s.tasks_completed || 0));
  const runTitle = finished ? 'Workload completed' : 'Time-boxed stress result';
  const runDetail = finished
    ? `All tasks closed in ${s.makespan_s.toFixed(1)} seconds.`
    : `${remaining} task${remaining === 1 ? '' : 's'} remained active when the ${s.sim_seconds.toFixed(1)} s evidence window ended.`;

  el('summary').innerHTML = `
    <div class="summary-live">
      <span>Playback progress</span>
      <strong id="progressTasks">0 / ${meta.tasks}</strong>
      <small id="progressTime">t = 0.0 s</small>
    </div>

    <div class="outcome-banner ${finished ? 'complete' : 'window'}">
      <span>${finished ? 'COMPLETE' : 'WINDOW ENDED'}</span>
      <div><strong>${runTitle}</strong><small>${runDetail}</small></div>
    </div>

    <dl>
      <dt>Tasks completed</dt>
      <dd>${s.tasks_completed} / ${s.tasks_announced}</dd>

      <dt>${finished ? 'Measured makespan' : 'Evidence window'}</dt>
      <dd>${(finished ? s.makespan_s : s.sim_seconds).toFixed(1)} s</dd>
      <dt>Robot&ndash;robot contacts</dt>
      <dd class="${s.contacts_robot_robot ? 'bad' : 'good'}">${s.contacts_robot_robot}</dd>
      <dt>Robot&ndash;human contacts</dt>
      <dd class="${s.contacts_robot_human ? 'bad' : 'good'}">${s.contacts_robot_human}</dd>
      <dt>Robot&ndash;rack contacts</dt>
      <dd class="${s.contacts_robot_rack ? 'bad' : 'good'}">${s.contacts_robot_rack}</dd>
      <dt>Closest observed separation</dt>
      <dd>${s.min_separation_m.toFixed(2)} m</dd>
      <dt>Safety-stop control ticks</dt>
      <dd>${Number(s.safety_stop_ticks || 0)}</dd>
      <dt>Energy-risk bids blocked</dt>
      <dd class="good">${Number(s.energy_bids_suppressed || 0)}</dd>
    </dl>
    <details class="run-diagnostics">
      <summary>Coordination diagnostics <i>⌄</i></summary>
      <dl>
        <dt>Deadlocks detected</dt><dd>${Number(s.deadlocks_detected || 0)}</dd>
        <dt>Pedestrian yield ticks</dt><dd>${Number(s.human_yield_ticks || 0)}</dd>
        <dt>Auction bids submitted</dt><dd>${Number(s.auction_bids_sent || 0)}</dd>
        <dt>Peer messages exchanged</dt><dd>${Number(s.msgs_sent || 0)}</dd>
        <dt>Broadcasts suppressed</dt><dd>${Number(s.heartbeat_messages_suppressed || 0) + Number(s.intent_messages_suppressed || 0) + Number(s.lease_renewals_suppressed || 0) + Number(s.bid_rebroadcasts_suppressed || 0)}</dd>
        <dt>Predictive hazards observed</dt><dd>${Number(s.predictive_hazards_seen || 0)}</dd>
        <dt>Predictive reroutes</dt><dd>${Number(s.predictive_reroutes || 0)}</dd>
        <dt>Experience-guided replans</dt><dd>${Number(s.experience_guided_replans || 0)}</dd>
        <dt>Charger contentions avoided</dt><dd>${Number(s.charger_contentions_avoided || 0)}</dd>
      </dl>
    </details>
    <p class="evidence-scope">Simulation evidence · ${contacts} observed contacts in this run · not a physical safety certification.</p>
  `;
}

function updateSummaryProgress(frame) {
  const done = Number.isFinite(frame.tasks_completed)
    ? frame.tasks_completed
    : (frame.fleet || []).reduce((sum, robot) => sum + (robot.done || 0), 0);

  const total = App.data.meta.tasks;

  const taskElement = el('progressTasks');
  const timeElement = el('progressTime');

  if (taskElement) {
    taskElement.textContent = `${done} / ${total}`;
  }

  if (timeElement) {
    timeElement.textContent = `t = ${frame.t.toFixed(1)} s`;
  }
}

boot();
