/* Shell: the menu, the keyboard, and the two transient overlays.
 *
 * Everything in here is chrome. It owns no simulation state and reads none - it
 * calls into window.BIOS, which main.js publishes once it has booted. That split
 * is deliberate: the interface can be reworked, re-skinned or thrown away without
 * touching a line of playback or telemetry code, which is the property the old
 * single-file dashboard did not have.
 *
 * The menu has two stages, and the first one is barely a menu.
 *
 *   STAGE ONE  Tab lays a rail down each edge - categories on the left, quick
 *              slots on the right - and leaves the middle of the screen alone.
 *              No blur, no dim, no scrim over the warehouse. You are still
 *              looking at the world; the menu is beside it, not over it. The HUD
 *              stays up for the same reason.
 *
 *   STAGE TWO  Enter, a number, or a click opens that category as a sheet over
 *              about three quarters of the screen. Now you have stopped to read,
 *              so the world dims, the rails fade back, and the HUD goes.
 *
 * Esc backs out one stage at a time. Tab closes the whole thing from anywhere.
 * That is the shape a pause screen has, and it is why the cursor on the left rail
 * is a moving band rather than a page of tabs: at stage one nothing has been
 * committed to yet.
 */

const Shell = (() => {
  const TABS = ['deployment', 'fleet', 'coordination', 'evidence', 'system'];
  const TITLES = {
    deployment:   ['Demo library', 'Deployment'],
    fleet:        ['Edge agents', 'Fleet'],
    coordination: ['Task allocation', 'Coordination'],
    evidence:     ['Run evidence', 'Evidence'],
    system:       ['Display and controls', 'System'],
  };
  const CAMERAS = [
    ['overview', '◉', 'Orbit'],
    ['tactical', '▦', 'Tactical'],
    ['follow',   '▶', 'Chase'],
    ['pov',      '◆', 'POV'],
  ];
  const CAMERA_LABELS = {
    overview: ['Orbit', 'Free camera · drag to look'],
    tactical: ['Tactical', 'Top-down · the whole floor'],
    follow:   ['Chase', 'Third person · behind the AMR'],
    pov:      ['Robot POV', 'First person · what the robot sees'],
  };
  // Categories that have nothing to show until a run exists. Deployment and
  // System are always reachable - one of them is how you get a run in the first
  // place, and the other is how you get out of trouble.
  const NEEDS_RUN = new Set(['fleet', 'coordination', 'evidence']);

  const el = id => document.getElementById(id);
  const body = document.body;

  let cursor = 0;          // which rail row the keyboard is on
  let openTab = null;      // which category the sheet is showing, if any
  let toastTimer = 0;
  let verdictTimer = 0;

  const hasRun = () => Boolean(window.BIOS?.app?.data);

  /* ------------------------------------------------------------ stage one */

  function isMenuOpen() { return body.classList.contains('menu-open'); }
  function isSheetOpen() { return body.classList.contains('sheet-open'); }

  function openMenu(tab) {
    body.classList.add('menu-open');
    el('menu').setAttribute('aria-hidden', 'false');
    if (tab) { moveCursorTo(tab); openSheet(tab); }
    else { syncRail(); renderQuick(); }
  }

  function closeMenu() {
    closeSheet();
    body.classList.remove('menu-open');
    el('menu').setAttribute('aria-hidden', 'true');
    // Focus can be left inside a hidden rail, which sends the next keystroke
    // somewhere invisible. Hand it back to the document.
    if (document.activeElement && el('menu').contains(document.activeElement)) {
      document.activeElement.blur();
    }
  }

  function toggleMenu() { isMenuOpen() ? closeMenu() : openMenu(); }

  /* Back out one stage. Esc from a sheet returns to the rails, which is where the
     reference leaves you too - closing the whole menu because you finished
     reading one page would be a different, more annoying interface. */
  function back() {
    if (isSheetOpen()) closeSheet();
    else closeMenu();
  }

  function railItems() { return [...document.querySelectorAll('.rail-item')]; }

  function syncRail() {
    const run = hasRun();
    railItems().forEach((item, index) => {
      const tab = item.dataset.tab;
      const empty = NEEDS_RUN.has(tab) && !run;
      item.classList.toggle('is-empty', empty);
      item.classList.toggle('active', index === cursor);
      item.setAttribute('aria-disabled', String(empty));
    });
  }

  function moveCursorTo(tab) {
    const index = TABS.indexOf(tab);
    if (index >= 0) cursor = index;
    syncRail();
  }

  function moveCursor(delta) {
    if (!isMenuOpen()) return;
    cursor = (cursor + delta + TABS.length) % TABS.length;
    syncRail();
  }

  /* ------------------------------------------------------------ stage two */

  function openSheet(tab) {
    if (!TABS.includes(tab)) return;
    if (NEEDS_RUN.has(tab) && !hasRun()) {
      toast('No run yet', 'Launch from Deployment first');
      moveCursorTo('deployment');
      return;
    }
    openTab = tab;
    moveCursorTo(tab);
    const [eyebrow, title] = TITLES[tab];
    el('sheetEyebrow').textContent = eyebrow;
    el('sheetTitle').textContent = title;
    for (const panel of document.querySelectorAll('.panel')) {
      panel.classList.toggle('active', panel.dataset.panel === tab);
    }
    body.classList.add('sheet-open');
    el('sheet').setAttribute('aria-hidden', 'false');
  }

  function closeSheet() {
    openTab = null;
    body.classList.remove('sheet-open');
    el('sheet')?.setAttribute('aria-hidden', 'true');
  }

  // Kept for callers that predate the two stages (main.js reaches for it).
  function showTab(tab) { openMenu(); openSheet(tab); }

  function stepTab(delta) {
    if (!isSheetOpen()) { moveCursor(delta); return; }
    // Skip categories that have nothing in them rather than opening an empty
    // sheet and making the reader work out why.
    for (let i = 1; i <= TABS.length; i++) {
      const next = TABS[(TABS.indexOf(openTab) + delta * i + TABS.length * i) % TABS.length];
      if (!NEEDS_RUN.has(next) || hasRun()) { openSheet(next); return; }
    }
  }

  /* --------------------------------------------------- the right-hand rail */

  function renderQuick() {
    renderQuickFleet();
    renderQuickCamera();
    renderCounter();
  }

  function renderQuickFleet() {
    const host = el('quickFleet');
    if (!host) return;
    const app = window.BIOS?.app;
    // Battery lives on the robots array, not on the fleet rows, and it has to be
    // read at the playhead rather than at frame zero or every slot shows 100%.
    const frame = app?.currentFrame?.() || app?.data?.frames?.[0];
    if (!frame || !frame.robots?.length) {
      host.innerHTML = '<p class="quick-empty">No fleet yet.</p>';
      return;
    }
    const info = new Map((frame.fleet || []).map(item => [item.id, item]));
    host.innerHTML = frame.robots.map(robot => {
      const state = String(info.get(robot.id)?.state || 'idle').replace(/_/g, ' ');
      const batt = Math.max(0, Math.min(100, Math.round((Number(robot.batt) || 0) * 100)));
      const cls = batt < 15 ? 'crit' : (batt < 35 ? 'low' : '');
      const colour = app.robotColour?.(robot.id) || 'var(--rim)';
      const active = robot.id === app.selectedRobotId ? ' active' : '';
      return `<button class="slot${active}" data-robot="${robot.id}" title="${robot.id} · ${batt}% · ${state}">
        <i class="slot-swatch" style="background:${colour}"></i>
        <b>${robot.id.replace(/^AMR/, '')}</b>
        <span class="slot-batt"><i class="${cls}" style="width:${batt}%"></i></span>
      </button>`;
    }).join('');
    for (const slot of host.querySelectorAll('[data-robot]')) {
      slot.addEventListener('click', () => {
        window.BIOS?.selectRobot?.(slot.dataset.robot);
        renderQuickFleet();
      });
    }
  }

  function renderQuickCamera() {
    const host = el('quickCamera');
    if (!host) return;
    const mode = window.BIOS?.app?.cameraMode;
    host.innerHTML = CAMERAS.map(([id, glyph, label]) =>
      `<button class="slot${id === mode ? ' active' : ''}" data-cam="${id}" title="${label}">
        <i>${glyph}</i><small>${label}</small>
      </button>`).join('');
    for (const slot of host.querySelectorAll('[data-cam]')) {
      slot.addEventListener('click', () => {
        window.BIOS?.setCameraMode?.(slot.dataset.cam);
        renderQuickCamera();
      });
    }
  }

  /* The counter in the corner. The reference puts the currency there - the number
     you are playing for. Ours is contacts, which is the number the whole safety
     argument rests on and the only headline that otherwise lives three clicks
     deep in Evidence. */
  function renderCounter() {
    const value = el('railCounter');
    if (!value) return;
    const summary = window.BIOS?.app?.data?.summary;
    if (!summary) {
      value.textContent = '—';
      value.className = '';
      return;
    }
    const contacts = Number(summary.contacts_robot_robot || 0)
      + Number(summary.contacts_robot_human || 0)
      + Number(summary.contacts_robot_rack || 0);
    value.textContent = String(contacts);
    value.className = contacts ? 'bad' : 'good';
  }

  /* --------------------------------------------------------------- overlays */

  /* The perspective toast. Deliberately not a notification queue: a second call
     replaces the first, because the only thing worth reading is where the camera
     is now. */
  function toast(text, sub) {
    const node = el('toast');
    if (!node) return;
    el('toastText').textContent = text;
    el('toastSub').textContent = sub || '';
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('show'), 1400);
  }

  function cameraToast(mode) {
    const [name, sub] = CAMERA_LABELS[mode] || [mode, 'Perspective'];
    toast(name, sub);
    if (isMenuOpen()) renderQuickCamera();
  }

  /* The run verdict. It fires when a run reaches its end and at no other time -
     a full-screen serif line that appears for everything stops meaning anything. */
  function verdict(text, sub, kind) {
    const node = el('verdict');
    if (!node) return;
    el('verdictText').textContent = text;
    el('verdictSub').textContent = sub || '';
    node.classList.toggle('window', kind === 'window');
    node.classList.add('show');
    clearTimeout(verdictTimer);
    verdictTimer = setTimeout(() => node.classList.remove('show'), 4200);
  }

  function clearVerdict() {
    clearTimeout(verdictTimer);
    el('verdict')?.classList.remove('show');
  }

  /* ------------------------------------------------------------- keyboard */

  // A keystroke aimed at a text field is not a shortcut. Everything below is
  // gated on this so typing a seed never scrubs the timeline.
  function isTyping(target) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || target.isContentEditable;
  }

  function onKey(event) {
    const bios = window.BIOS || {};
    const typing = isTyping(event.target);

    if (event.key === 'Escape') {
      if (body.classList.contains('jury-mode')) { bios.togglePresentationMode?.(); return; }
      if (isMenuOpen()) { event.preventDefault(); back(); }
      return;
    }

    // Tab is the menu key. Inside a field it stays a focus key, so a form is
    // still navigable; everywhere else it summons and dismisses.
    if (event.key === 'Tab' && !typing) {
      event.preventDefault();
      toggleMenu();
      return;
    }

    if (typing) return;

    // While the rails are up without a sheet, the arrows drive the cursor rather
    // than the timeline - that is what they are for on a pause screen.
    if (isMenuOpen() && !isSheetOpen()) {
      if (event.code === 'ArrowUp' || event.code === 'KeyW') { event.preventDefault(); moveCursor(-1); return; }
      if (event.code === 'ArrowDown' || event.code === 'KeyS') { event.preventDefault(); moveCursor(1); return; }
      if (event.code === 'Enter' || event.code === 'NumpadEnter') {
        event.preventDefault();
        openSheet(TABS[cursor]);
        return;
      }
    }

    switch (event.code) {
      case 'Space':
        event.preventDefault();
        bios.togglePlay?.();
        break;
      case 'F5':
        // Reload is not what anyone wants from a running simulation, and this is
        // the key every player already associates with changing the view.
        event.preventDefault();
        bios.cycleCameraMode?.();
        break;
      case 'KeyV':
        bios.cycleCameraMode?.();
        break;
      case 'KeyC':
        bios.cycleTargetRobot?.();
        if (isMenuOpen()) renderQuickFleet();
        break;
      case 'KeyZ':
        bios.adjustZoom?.(-0.4);
        break;
      case 'KeyX':
        bios.adjustZoom?.(0.4);
        break;
      case 'KeyJ':
        bios.togglePresentationMode?.();
        break;
      case 'F1':
        event.preventDefault();
        body.classList.toggle('hud-hidden');
        toast(body.classList.contains('hud-hidden') ? 'HUD off' : 'HUD on', 'Overlay');
        break;
      case 'F11':
        event.preventDefault();
        bios.toggleFullscreen?.();
        break;
      case 'ArrowLeft':
        event.preventDefault();
        bios.step?.(-1);
        break;
      case 'ArrowRight':
        event.preventDefault();
        bios.step?.(1);
        break;
      case 'KeyQ':
        if (isMenuOpen()) stepTab(-1);
        break;
      case 'KeyE':
        if (isMenuOpen()) stepTab(1);
        break;
      case 'Digit1': case 'Digit2': case 'Digit3': case 'Digit4': case 'Digit5': {
        const index = Number(event.code.slice(-1)) - 1;
        if (!isMenuOpen()) openMenu();
        openSheet(TABS[index]);
        break;
      }
    }
  }

  /* ------------------------------------------------------------------ boot */

  function init() {
    for (const item of railItems()) {
      item.addEventListener('click', () => openSheet(item.dataset.tab));
      item.addEventListener('mouseenter', () => { if (!isSheetOpen()) moveCursorTo(item.dataset.tab); });
    }
    el('openMenuBtn')?.addEventListener('click', () => openMenu());
    el('closeMenuBtn')?.addEventListener('click', closeMenu);
    el('openPanelBtn')?.addEventListener('click', () => openSheet(TABS[cursor]));
    el('sheetCloseBtn')?.addEventListener('click', closeSheet);

    // Clicking the world through the gap between the rails dismisses the menu,
    // the way clicking out of a pause screen does. Clicks on a rail or inside the
    // sheet must not - and #menu is pointer-events:none, so only the sheet's own
    // backdrop area can produce this.
    el('menu')?.addEventListener('pointerdown', event => {
      if (event.target === el('menu')) back();
    });

    window.addEventListener('keydown', onKey);
    syncRail();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }

  return {openMenu, closeMenu, toggleMenu, showTab, openSheet, closeSheet, back,
          isMenuOpen, isSheetOpen, syncRail, renderQuick, toast, cameraToast,
          verdict, clearVerdict};
})();

window.Shell = Shell;
