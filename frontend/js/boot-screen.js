/* The boot screen: what the page shows while the first run is being computed.
 *
 * The problem it solves is not decorative. boot() ends with run(), and run() is
 * a POST that simulates 320 s of Chokepoint before it answers - about six
 * seconds on the demo machine. For those six seconds the page was already
 * painted: an empty world, a HUD reading 0 / 0, three greyed rail categories.
 * It looks broken rather than busy, and it is the first thing a judge sees.
 *
 * So the screen is a power-on self-test, which is the one loader this project
 * has earned the right to: the team is called BIOS. Every line in the readout is
 * a real stage reporting its real elapsed time - nothing here is a timer
 * pretending to be progress. If the simulation takes six seconds the readout
 * says so, and if it fails the readout says that instead and lets you in anyway.
 *
 * It owns the keyboard while it is up. The handler runs in the CAPTURE phase and
 * calls stopImmediatePropagation, so shell.js never sees the keystroke that
 * dismisses this - otherwise Space would dismiss the boot screen *and* toggle
 * playback, and F11 would dismiss it and go fullscreen.
 *
 * The boundary is the same one shell.js keeps: this file owns no simulation
 * state and reads none. main.js reports into it through window.BiosBoot and
 * never reads back:
 *
 *   BiosBoot.stage(id)        a stage finished; stamps its elapsed time
 *   BiosBoot.fail(id, msg)    a stage failed; says so and lets you in anyway
 *   BiosBoot.ready()          the world is worth looking at; offers the way in
 *   BiosBoot.show()           put it back up - for a presenter resetting the
 *                             room between panels, since F5 is the camera key
 *                             here and reloading costs a six-second rerun
 *   BiosBoot.dismiss()        take it down
 *
 * Every one of them is a no-op once the screen is down, so main.js can report
 * unconditionally and the second and later runs cost nothing.
 */

window.BiosBoot = (() => {
  const el = id => document.getElementById(id);
  const root = el('boot');

  // Nothing to own if the markup is not there. A missing boot screen must not
  // take the page down with it - every reporting call below is a no-op then.
  if (!root) {
    const noop = () => {};
    return {stage: noop, fail: noop, ready: noop, dismiss: noop, show: noop,
            isUp: () => false};
  }

  /* Weighted because the stages are not the same size. The simulation is most of
     the wait and the bar has to look like it knows that, or it sits at 30% for
     six seconds and then jumps to done. */
  const STAGES = [
    ['interface',  6],
    ['sprites',   14],
    ['library',    8],
    ['simulation', 56],
    ['warehouse',  16],
  ];
  const WEIGHT = new Map(STAGES);

  // How long the entrance takes to play out. The prompt is held until both this
  // and the real work have finished, so a fast machine does not flash the logo
  // for a third of a second and call it a presentation.
  const ENTRANCE_MS = 1700;

  /* If ready() never arrives - a hung backend, a stage that throws before it
     reports - arm the prompt anyway. Being stuck on a splash screen with no way
     forward is worse than any load it was covering.

     Deliberately generous. The measured opening is 6-9 s (320 s of Chokepoint),
     and the error cases do not need this at all: fail() arms the screen the
     moment a stage reports a failure, with the reason on the prompt. So this
     only covers "nothing reported anything", and firing it early is the one way
     this screen could reintroduce the bug it exists to fix - it would hand
     someone a half-built warehouse and call it ready. */
  const FAILSAFE_MS = 40000;

  const t0 = performance.now();
  const done = new Set();
  let armed = false;      // is the prompt up / will input dismiss
  let up = true;
  let failsafe = 0;

  const line = id => root.querySelector(`[data-stage="${id}"]`);

  /* --------------------------------------------------------------- readout */

  function elapsed(ms) {
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
  }

  function progress() {
    let sum = 0;
    for (const id of done) sum += WEIGHT.get(id) || 0;
    const bar = el('bootProgress');
    if (bar) bar.style.setProperty('--fill', `${sum}%`);
    // The stage currently being waited on gets the shimmer, so the one line that
    // is actually costing time is the one that looks alive.
    for (const [id] of STAGES) {
      const node = line(id);
      if (node) node.classList.toggle('is-working', !done.has(id) && isNext(id));
    }
  }

  function isNext(id) {
    for (const [candidate] of STAGES) {
      if (!done.has(candidate)) return candidate === id;
    }
    return false;
  }

  function stage(id) {
    if (!up || done.has(id)) return;
    done.add(id);
    const node = line(id);
    if (node) {
      node.classList.remove('is-working');
      node.classList.add('is-done');
      const value = node.querySelector('.boot-stage-value');
      if (value) value.textContent = elapsed(performance.now() - t0);
    }
    progress();
  }

  function fail(id, message) {
    if (!up) return;
    const node = line(id);
    if (node) {
      node.classList.remove('is-working');
      node.classList.add('is-bad');
      const value = node.querySelector('.boot-stage-value');
      if (value) value.textContent = 'FAIL';
    }
    // A failed stage still counts toward the bar. The bar tracks how far the
    // boot got, not how well it went - the line above says how well it went.
    done.add(id);
    progress();
    setPrompt(message || 'boot incomplete', true);
    arm();
  }

  /* ----------------------------------------------------------------- prompt */

  function setPrompt(text, bad) {
    const node = el('bootPrompt');
    if (!node) return;
    node.textContent = text;
    node.classList.toggle('is-bad', Boolean(bad));
  }

  function arm() {
    if (armed || !up) return;
    armed = true;
    clearTimeout(failsafe);
    root.classList.add('is-armed');
  }

  /* Called when the world behind this screen is actually worth looking at.
     Holds the prompt back until the entrance has finished playing, so the
     ceremony is the same length on a fast machine as on a slow one. */
  function ready() {
    if (!up || armed) return;
    setPrompt('press any key to enter');
    const remaining = Math.max(0, ENTRANCE_MS - (performance.now() - t0));
    setTimeout(arm, remaining);
  }

  /* ---------------------------------------------------------------- dismiss */

  function dismiss() {
    if (!up) return;
    up = false;
    clearTimeout(failsafe);
    root.classList.add('is-leaving');
    root.setAttribute('aria-hidden', 'true');
    // Let the exit transition run, then take it out of the layer stack entirely
    // so it can never eat a pointer event. transitionend is not reliable when
    // the element is display:none'd by something else mid-flight, so the
    // timeout is the one that actually does it.
    setTimeout(() => {
      root.hidden = true;
      root.classList.remove('is-leaving');
    }, 760);
  }

  function show() {
    up = true;
    armed = true;                 // a re-shown screen is dismissible at once
    root.hidden = false;
    root.removeAttribute('aria-hidden');
    root.classList.remove('is-leaving');
    root.classList.add('is-armed');
    setPrompt('press any key to enter');
  }

  /* ---------------------------------------------------------------- input */

  // Shift/Ctrl/Alt/Meta on their own are not a decision - somebody reaching for
  // a shortcut should not lose the screen before they finish the chord.
  const MODIFIERS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock',
                             'NumLock', 'ScrollLock', 'ContextMenu']);

  function swallow(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  /* One press produces a burst - pointerdown, mousedown, mouseup, click - and
     only the first of them is consumed while the screen is up. Without this the
     rest land on the world the moment it is uncovered, so the gesture that
     dismissed the boot screen also picks a robot or swings the camera. Hold the
     whole burst for a beat after dismissal. */
  let swallowUntil = 0;

  function onKey(event) {
    if (!up) {
      if (performance.now() < swallowUntil) swallow(event);
      return;
    }
    if (MODIFIERS.has(event.key)) return;
    // F12 and the devtools chord belong to the browser, not to us.
    if (event.key === 'F12') return;
    swallow(event);
    if (armed) { swallowUntil = performance.now() + 700; dismiss(); }
    else nudge();
  }

  function onPointer(event) {
    if (!up) {
      if (performance.now() < swallowUntil) swallow(event);
      return;
    }
    swallow(event);
    if (armed) { swallowUntil = performance.now() + 700; dismiss(); }
    else nudge();
  }

  /* Input before the world is ready. Refusing silently reads as a frozen page,
     which is the exact impression this screen exists to prevent, so say what is
     being waited on and flash the line that is costing the time. */
  let nudging = 0;
  function nudge() {
    const readout = el('bootReadout');
    if (!readout) return;
    clearTimeout(nudging);
    readout.classList.add('is-nudged');
    nudging = setTimeout(() => readout.classList.remove('is-nudged'), 520);
  }

  /* Every pointer event in the family, not just pointerdown.
     "Dismissed by a click" has to mean dismissed by a click, and a plain `click`
     is all some input paths produce - a presentation clicker, an accessibility
     tool driving the page, a synthetic dispatch. Pointerdown alone left those
     unable to get past this screen at all, which is the one bug it must not
     have. dismiss() is idempotent, so the duplicates a real mouse fires cost
     nothing and the burst is swallowed by the window above. */
  window.addEventListener('keydown', onKey, true);
  for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click', 'touchstart']) {
    // passive:false explicitly - touchstart on window is passive by default, and
    // calling preventDefault inside a passive listener is ignored with a console
    // warning rather than honoured.
    window.addEventListener(type, onPointer, {capture: true, passive: false});
  }

  /* ------------------------------------------------------------------ init */

  /* The entrance wipes the logo in under a mask, and a mask is arithmetic that
     can be wrong - get the end position off by a hair and `forwards` pins a
     logo nobody can see. So the mask is dropped outright the moment the scan
     has played: after that the logo is just an image, and its resting state
     depends on nothing. The timeout covers the case where the animation never
     runs at all and animationend therefore never fires. */
  const logo = root.querySelector('.boot-logo');
  if (logo) {
    const scanned = () => logo.classList.add('is-scanned');
    logo.addEventListener('animationend', event => {
      if (event.animationName === 'bootScan') scanned();
    });
    setTimeout(scanned, 2400);
  }

  // The interface stage is this file running at all: the markup parsed, the
  // stylesheet applied, the screen painted. It is the one stage that is already
  // true by the time anything can report it.
  stage('interface');
  progress();
  failsafe = setTimeout(() => {
    setPrompt('taking longer than expected · press any key to enter', true);
    arm();
  }, FAILSAFE_MS);

  return {stage, fail, ready, dismiss, show, isUp: () => up};
})();
