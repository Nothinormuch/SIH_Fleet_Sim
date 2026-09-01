/* The heads-up display: three vitals, a message rail, and the mission bar.
 *
 * This replaces a HUD that had grown to nine floating cards - a completion ring,
 * a minimap, a health ring, a fleet grid, an allocation table, an auction panel
 * and a ticker - all of them drawn on top of the warehouse we were asking people
 * to look at. Every one of those readouts still exists; they moved into the
 * command menu, where you can read them, instead of hovering over the thing they
 * describe, where you cannot.
 *
 * What survives here is only what answers a question at a glance:
 *
 *   WORKLOAD        how much of the job is done
 *   FLEET CHARGE    whether the fleet can keep doing it
 *   SAFETY MARGIN   how close anything has come to touching, right now
 *
 * The bars are borrowed straight from a souls HUD, including the trailing ghost:
 * a change you can see move is worth more than a number that was different last
 * frame, and during a demo nobody is staring at the corner waiting for a digit.
 *
 * One rule matters more than the styling. TASK COUNTS COME FROM ONE PLACE.
 * The simulation publishes frame.tasks_completed; this file and the menu both
 * read it and neither re-derives it by summing per-robot counters, which is how
 * the old dashboard managed to show three different totals on one screen.
 */

const Hud = (() => {
  const el = id => document.getElementById(id);

  const state = {
    ready: false,
    meta: null,
    summary: null,
    events: [],      // auction + decision, merged and time-sorted
    lastKey: '',     // avoids rebuilding the rail when nothing new has arrived
  };

  const nodes = {};

  function cache() {
    for (const key of ['vitalTasks', 'vitalTasksVal', 'vitalTasksFill', 'vitalTasksGhost',
                       'vitalCharge', 'vitalChargeVal', 'vitalChargeFill', 'vitalChargeGhost',
                       'vitalMargin', 'vitalMarginVal', 'vitalMarginFill', 'vitalMarginGhost',
                       'missionFill', 'messages']) {
      nodes[key] = el(key);
    }
  }

  /* ---------------------------------------------------------------- events */

  /* The same de-duplication main.js applies to the auction log. A task announced
     at t=12 stays in every telemetry frame until it is claimed, so the raw event
     stream repeats it ten times a second; counting or listing it verbatim is how
     you end up telling a judge there were thirty thousand announcements. */
  function dedupe(events) {
    const seen = new Set();
    return events.filter(event => {
      let key;
      if (event.type === 'TN') key = `TN|${event.task}|${event.e ?? 0}`;
      else if (event.type === 'AW') key = `AW|${event.task}|${event.e ?? 0}|${event.winner || event.dst || event.src}`;
      else if (event.type === 'TD') key = `TD|${event.task}|${event.src}`;
      else return false;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  /* Bids are excluded on purpose. There are thousands of them in a long run and
     they are all the same sentence; the count belongs in the auction panel, and
     the rail is for the moments that change what the fleet is doing. */
  function collectEvents(payload) {
    const auction = dedupe((payload.frames || []).flatMap(frame => frame.auction_events || []));
    const rows = auction.map(event => {
      const task = event.task || 'task';
      if (event.type === 'TN') return {t: event.t, kind: 'TN', who: 'WMS', text: `announced ${task}`};
      if (event.type === 'AW') return {t: event.t, kind: 'AW', who: event.winner || event.dst || event.src, text: `won ${task}`};
      return {t: event.t, kind: 'TD', who: event.src, text: `completed ${task}`};
    });

    // TASK_ACCEPTED and TASK_COMPLETED restate the award and the completion the
    // auction feed already carries, so including them puts every task on the rail
    // twice at the same timestamp. What is left is the part only BIOS 6 produces -
    // a reroute around a predicted hazard, a charger chosen against contention, an
    // idle robot clearing a lane - which is also the part worth showing a judge.
    const ECHOES = new Set(['TASK_ACCEPTED', 'TASK_COMPLETED']);
    const seenDecision = new Set();
    for (const frame of payload.frames || []) {
      for (const robot of frame.fleet || []) {
        const decision = robot.decision;
        if (!decision || ECHOES.has(decision.code)) continue;
        const key = `${decision.robot}|${decision.t}|${decision.code}`;
        if (seenDecision.has(key)) continue;
        seenDecision.add(key);
        rows.push({
          t: Number(decision.t) || 0,
          kind: 'DC',
          who: decision.robot,
          text: String(decision.code || '').replace(/_/g, ' ').toLowerCase(),
        });
      }
    }
    rows.sort((a, b) => a.t - b.t);
    return rows;
  }

  const rowKey = row => `${row.t.toFixed(2)}|${row.kind}|${row.who}|${row.text}`;

  /* Reconcile the rail against its keys instead of rewriting it.
   *
   * The obvious version - rebuild innerHTML whenever the visible set changes -
   * looks fine until the floor gets busy, and then it is subtly broken: rows
   * arrive several times a second, every rebuild restarts the 400 ms entry fade
   * on every row, and nothing ever reaches full opacity. The rail ends up
   * permanently at about 45% and reads as a rendering fault rather than as text.
   *
   * So keep the nodes that are still on screen and only build the ones that are
   * new. A row that has been there for two seconds keeps its finished animation,
   * and the fade means "this just happened" again.
   */
  function renderMessages(t) {
    const host = nodes.messages;
    if (!host) return;

    let end = 0;
    while (end < state.events.length && state.events[end].t <= t + 1e-6) end++;
    const recent = state.events.slice(Math.max(0, end - 5), end);
    const keys = recent.map(rowKey);
    const signature = keys.join('~');
    if (signature === state.lastKey) return;
    state.lastKey = signature;

    const existing = new Map();
    for (const node of [...host.children]) existing.set(node.dataset.k, node);

    // Drop what has scrolled off the top. Everything else must stay where it is:
    // re-inserting a node restarts its CSS animation, so replaceChildren - even
    // with the same nodes - is just as destructive as rewriting innerHTML.
    for (const [key, node] of existing) {
      if (!keys.includes(key)) {
        node.remove();
        existing.delete(key);
      }
    }

    for (let i = 0; i < recent.length; i++) {
      const row = recent[i];
      let node = existing.get(keys[i]);
      if (!node) {
        node = document.createElement('div');
        node.dataset.k = keys[i];
        node.innerHTML = `<small>${row.t.toFixed(1)}s</small>` +
          `<span><b>${escape(row.who)}</b> ${escape(row.text)}</span>`;
        // The window slides forward in time, so anything new is newer than
        // everything already here and belongs at the end.
        host.appendChild(node);
      }
      // Oldest line at the top and most faded, newest at the bottom at full
      // strength, so the rail reads downward the way it fills. Only the class
      // changes here; the animation name does not, so survivors are not restarted.
      node.className = `message kind-${row.kind} age-${recent.length - 1 - i}`;
    }
  }

  function escape(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
  }

  /* ---------------------------------------------------------------- vitals */

  function setBar(fill, ghost, fraction) {
    const pct = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
    if (fill) fill.style.width = pct;
    if (ghost) ghost.style.width = pct;
  }

  /* The tightest gap between any two moving bodies at this instant. The run
     summary reports the minimum over the whole run, which is the right number for
     evidence and the wrong one for a live bar - a single tight pass at t=40 would
     peg the HUD red for the remaining eight minutes. */
  function liveSeparation(frame) {
    const bodies = [...(frame.robots || []), ...(frame.humans || [])];
    let min = Infinity;
    for (let i = 0; i < bodies.length; i++) {
      for (let j = i + 1; j < bodies.length; j++) {
        const dx = bodies[i].x - bodies[j].x;
        const dy = bodies[i].y - bodies[j].y;
        const d = Math.hypot(dx, dy);
        if (d < min) min = d;
      }
    }
    return Number.isFinite(min) ? min : null;
  }

  function renderVitals(frame, meta) {
    // Workload. Authoritative count, published by the simulation.
    const done = Number.isFinite(frame.tasks_completed) ? frame.tasks_completed : 0;
    const total = Math.max(1, Number(meta.tasks) || 1);
    if (nodes.vitalTasksVal) nodes.vitalTasksVal.textContent = `${done} / ${meta.tasks}`;
    setBar(nodes.vitalTasksFill, nodes.vitalTasksGhost, done / total);
    if (nodes.missionFill) nodes.missionFill.style.width = `${Math.min(1, done / total) * 100}%`;

    // Fleet charge, meaned across the fleet, against the reserve floor the
    // energy gate is defending.
    const robots = frame.robots || [];
    const charge = robots.length
      ? robots.reduce((sum, robot) => sum + (Number(robot.batt) || 0), 0) / robots.length
      : 0;
    const reserve = Number(meta.energy_reserve_frac) || 0.15;
    if (nodes.vitalChargeVal) nodes.vitalChargeVal.textContent = `${Math.round(charge * 100)}%`;
    setBar(nodes.vitalChargeFill, nodes.vitalChargeGhost, charge);
    if (nodes.vitalCharge) {
      nodes.vitalCharge.classList.toggle('is-low', charge < reserve * 2 && charge >= reserve);
      nodes.vitalCharge.classList.toggle('is-crit', charge < reserve);
    }

    // Safety margin. Full bar at three body-widths of clearance, empty at
    // contact - so the bar drains as the floor gets busy, which is the shape a
    // person already knows how to read.
    const diameter = Number(meta.robot_diameter_m) || 0.8;
    const gap = liveSeparation(frame);
    const span = diameter * 3;
    const fraction = gap === null ? 1 : Math.max(0, Math.min(1, (gap - diameter) / (span - diameter)));
    if (nodes.vitalMarginVal) {
      nodes.vitalMarginVal.textContent = gap === null ? '—' : `${gap.toFixed(2)} m`;
    }
    setBar(nodes.vitalMarginFill, nodes.vitalMarginGhost, fraction);
    if (nodes.vitalMargin) {
      nodes.vitalMargin.classList.toggle('is-low', fraction < 0.45 && fraction >= 0.18);
      nodes.vitalMargin.classList.toggle('is-crit', fraction < 0.18);
    }
  }

  /* ------------------------------------------------------------------ API */

  // Signature kept from the previous HUD so main.js needs no special case: it
  // hands over the view and the sprite atlas even though neither is drawn on any
  // more, now that the overlay is DOM rather than a second canvas.
  function init(_view, _imgs, payload) {
    cache();
    state.meta = payload.meta;
    state.summary = payload.summary;
    state.events = collectEvents(payload);
    state.lastKey = '';
    state.ready = true;
    if (nodes.messages) nodes.messages.innerHTML = '';
  }

  function render(frame, summary, meta, t) {
    if (!state.ready || !frame || !meta) return;
    renderVitals(frame, meta);
    renderMessages(Number.isFinite(t) ? t : frame.t);
  }

  function resize() { /* the overlay is laid out by CSS; nothing to re-measure */ }

  function dispose() {
    state.ready = false;
    state.events = [];
    state.lastKey = '';
    if (nodes.messages) nodes.messages.innerHTML = '';
  }

  return {init, render, resize, dispose};
})();

window.Hud = Hud;
