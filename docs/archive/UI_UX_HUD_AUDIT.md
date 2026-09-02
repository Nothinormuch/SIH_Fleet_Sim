# HUD UI/UX audit and responsive contract

## Operator decision hierarchy

The canvas now defaults to **Core HUD**: completion, scenario, coordination health,
camera controls, and optional PiP. Fleet rows, auction events, allocation totals, and
the final run report remain available in the persistent side panel. This removes
duplicate floating information from the primary map while preserving it for diagnosis.

**HUD details** is an explicit disclosure for a tactical auction view. Tactical HUD and
PiP are mutually exclusive because both need the same lower canvas region. Opening one
closes the other rather than allowing a silent overlap. At stacked/tablet widths the
tactical view reduces to auction and allocation cards; detailed fleet data remains in
the scrolling panel.

## Responsive behavior

- Wide desktop: canvas plus 360 px operator panel; PiP opens above, not on top of, the
  legend and playback transport.
- Laptop: PiP becomes shorter; the core cards stay below the camera toolbar.
- Tablet and smaller: canvas and operator panel stack, PiP starts closed, and tactical
  mode shows only the two allocation cards that matter to the auction decision.
- Phone: the toolbar uses compact PiP/HUD labels, hides redundant zoom controls, all
  run controls become one column, and the legend scrolls within its own row.
- Reduced-motion preference disables decorative spinning and transitions.

## Accessibility basis

- WCAG 2.2 Reflow calls for content to retain information and functionality at a width
  equivalent to 320 CSS pixels, except genuinely two-dimensional content such as maps.
- WCAG 2.2 Target Size (Minimum) uses 24 by 24 CSS pixels or sufficient spacing. PiP
  tools and the scrub control now provide at least a 24 px target box.
- WCAG 2.2 Focus Not Obscured requires focused controls not to be hidden by authored
  overlays. Core/tactical disclosure, real layout rows, and mutually exclusive PiP
  prevent those collisions.
- The WAI-ARIA disclosure pattern uses a button with `aria-expanded` and optional
  `aria-controls`; the HUD button implements both and works with Enter/Space natively.
- Keyboard focus uses a high-contrast two-layer outline, while `prefers-reduced-motion`
  is respected.

Primary references:

- <https://www.w3.org/WAI/WCAG22/Understanding/reflow>
- <https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html>
- <https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html>
- <https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/>
- <https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Containment/Container_queries>

## Render verification

The browser collision audit checks the camera toolbar, legend, transport, PiP, and each
HUD card for pairwise intersections and viewport overflow. Both Core and Tactical modes
were exercised at 1440x900, 1280x720, 1024x768, 768x1024, and 390x844. The final matrix
reported zero intersections and zero viewport overflow at every size. Browser console
errors and warnings were also empty.
