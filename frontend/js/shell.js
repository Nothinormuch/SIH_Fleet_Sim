/* Shell: the menu, the keyboard, and the two transient overlays.
 *
 * Everything in here is chrome. It owns no simulation state and reads none - it
 * calls into window.BIOS, which main.js publishes once it has booted. That split
 * is deliberate: the interface can be reworked, re-skinned or thrown away without
 * touching a line of playback or telemetry code, which is the property the old
 * single-file dashboard did not have.
 *
 * Three ideas are borrowed on purpose.
 *
 *   The menu veils the world instead of replacing it. You are always looking at
 *   the warehouse; the categories are a layer over it. That is why the backdrop
 *   blurs rather than fills, and why playback keeps running underneath.
 *
 *   Five categories, flat, all visible, reachable by number. No submenus, no
 *   accordions, no disclosure triangles hiding a panel someone needs mid-demo.
 *
 *   The camera announces itself and then shuts up. F5 cycles the perspective and
 *   a word appears in the middle of the screen for a moment - which is strictly
 *   more legible than a permanent readout nobody looks at.
 */

const Shell = (() => {
  const TABS = ['deployment', 'fleet', 'coordination', 'evidence', 'system'];
  const CAMERA_LABELS = {
    overview: ['Orbit', 'Free camera · drag to look'],
    tactical: ['Tactical', 'Top-down · the whole floor'],
    follow:   ['Chase', 'Third person · behind the AMR'],
    pov:      ['Robot POV', 'First person · what the robot sees'],
  };

  const el = id => document.getElementById(id);
  const body = document.body;

  let activeTab = 'deployment';
  let toastTimer = 0;
  let verdictTimer = 0;

  /* ------------------------------------------------------------------ menu */

  function isMenuOpen() { return body.classList.contains('menu-open'); }

  function openMenu(tab) {
    body.classList.add('menu-open');
    el('menu').setAttribute('aria-hidden', 'false');
    if (tab) showTab(tab);
  }

  function closeMenu() {
    body.classList.remove('menu-open');
    el('menu').setAttribute('aria-hidden', 'true');
    // Focus can be left inside a hidden panel, which makes the next keystroke go
    // somewhere invisible. Hand it back to the document so the shortcuts work.
    if (document.activeElement && el('menu').contains(document.activeElement)) {
      document.activeElement.blur();
    }
  }

  function toggleMenu() { isMenuOpen() ? closeMenu() : openMenu(); }

  function showTab(name) {
    if (!TABS.includes(name)) return;
    activeTab = name;
    for (const tab of document.querySelectorAll('.tab')) {
      const on = tab.dataset.tab === name;
      tab.classList.toggle('active', on);
      tab.setAttribute('aria-selected', String(on));
    }
    for (const panel of document.querySelectorAll('.panel')) {
      panel.classList.toggle('active', panel.dataset.panel === name);
    }
  }

  function stepTab(delta) {
    const next = (TABS.indexOf(activeTab) + delta + TABS.length) % TABS.length;
    showTab(TABS[next]);
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

    // Esc closes the menu from anywhere, including out of a focused field.
    if (event.key === 'Escape') {
      if (body.classList.contains('jury-mode')) { bios.togglePresentationMode?.(); return; }
      if (isMenuOpen()) { event.preventDefault(); closeMenu(); }
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
        openMenu(TABS[index]);
        break;
      }
    }
  }

  /* ------------------------------------------------------------------ boot */

  function init() {
    for (const tab of document.querySelectorAll('.tab')) {
      tab.addEventListener('click', () => showTab(tab.dataset.tab));
    }
    el('openMenuBtn')?.addEventListener('click', () => openMenu());
    el('closeMenuBtn')?.addEventListener('click', closeMenu);

    // Clicking the darkened world behind the menu dismisses it, the way clicking
    // out of a pause screen does. Clicks inside the menu body must not.
    el('menu')?.addEventListener('pointerdown', event => {
      if (event.target === el('menu')) closeMenu();
    });

    window.addEventListener('keydown', onKey);
    showTab('deployment');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }

  return {openMenu, closeMenu, toggleMenu, showTab, isMenuOpen, toast, cameraToast, verdict, clearVerdict};
})();

window.Shell = Shell;
