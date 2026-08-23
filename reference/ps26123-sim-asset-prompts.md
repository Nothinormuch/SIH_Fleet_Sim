---
title: SIH26123 — simulation asset generation prompt pack
tags: [sih-2026, assets, prompts]
type: reference
created: 2026-08-23
---

# SIH26123 — image-generation prompt pack for the AMR fleet sim

Assets for the decentralized AMR fleet simulation ([[sih-2026-ps26123-amr-fleet-critique]]).
Decisions and rationale behind this pack: [[sih-2026-ps26123-sim-asset-pipeline]].

## 0. Three decisions baked in

| Decision | Choice | Why |
| --- | --- | --- |
| Camera | Orthographic top-down (0° tilt) | The planner is a 2D grid/graph. Isometric art fights the collision math. |
| Tile unit | 256 × 256 px = 1 grid cell | Power-of-two; downscales to 64px for the live sim, upscales for the demo video. |
| Robot orientation | Always drawn facing UP (north / +Y) at 0° | Code rotates the sprite. Never let the generator pick a diagonal pose. |

---

## 1. STYLE BIBLE — prepend to every asset prompt

```
STYLE: Clean modern flat-vector illustration with subtle soft-3D shading —
the look of a premium robotics operations dashboard. Crisp geometric edges,
2px dark outlines, minimal gradients, no textures, no noise, no grain.
Slightly rounded corners on all hard surfaces.

CAMERA: Strict orthographic top-down bird's-eye view, camera perfectly
perpendicular to the floor, zero perspective, zero vanishing point, no tilt.
The object is seen from directly above.

LIGHTING: Flat even ambient light from above. One soft contact shadow
directly beneath the object, 15% opacity, no directional cast shadow.

PALETTE (use only these):
  floor dark slate    #1B2029
  floor panel         #232A35
  grid line           #2E3846
  steel rack          #4A5666
  light steel         #6B7889
  robot chassis       #E8EDF2
  chassis trim        #333B47
  safety yellow       #F2C14E
  accent cyan         #22D3EE
  accent amber        #F59E0B
  accent magenta      #E879F9
  status green        #34D399
  alert red           #F43F5E
  cardboard           #C08A4E
  pallet wood         #8B5E34

OUTPUT: Single centered object, fully transparent background (PNG alpha).
If transparency is unavailable, place the object on a solid pure magenta
#FF00FF background with no shadow touching the edges, for chroma keying.
Square 1:1 canvas, object occupies 85% of frame with even margin.
```

Universal negative prompt:

```
NEGATIVE: perspective, vanishing point, isometric, 3/4 view, tilted camera,
photorealism, photograph, dramatic lighting, lens flare, bloom, motion blur,
depth of field, drop shadows on background, gradient background, sky, horizon,
people (unless requested), text, letters, numbers, watermark, logo, signature,
UI chrome, frame, border, drop shadow box, cluttered background, grunge,
rust, dirt, noise, film grain, sketch lines, hand-drawn wobble
```

---

## 2. Core sim assets

### 2.1 AMR robot — base chassis (hero asset, get this right before anything else)

```
[STYLE BIBLE]

SUBJECT: A single autonomous mobile robot (AMR) warehouse transport robot,
viewed from directly above. Rectangular rounded-corner chassis, roughly
3:4 aspect (slightly taller than wide), facing straight UP toward the top
of the frame.

DETAILS:
- Body: off-white #E8EDF2 top deck with a dark #333B47 recessed border band
  running around the full perimeter.
- Front edge (top of frame): a wide dark sensor bar with three small cyan
  #22D3EE LIDAR/depth-sensor dots evenly spaced.
- Center: a circular flat load-carrying turntable plate in light steel
  #6B7889 with four small bolt dots at compass points.
- Rear edge (bottom of frame): a slim recessed handle notch and two small
  dark vents.
- Left and right long edges: a continuous thin light-strip channel, currently
  unlit / neutral dark grey.
- Four small dark wheel housings visible as rounded rectangles just inside
  each corner.
- A single small round emergency-stop button in alert red #F43F5E at the
  top-right corner of the deck.

[NEGATIVE]
```

Fleet recolors — append one line, keep everything else byte-identical:

- **AMR-01**: `the side light-strip channels glow accent cyan #22D3EE, and a matching cyan ring outlines the center turntable.`
- **AMR-02**: same with accent amber `#F59E0B`
- **AMR-03**: same with accent magenta `#E879F9`
- **AMR-04**: same with status green `#34D399`

### 2.2 Robot state overlays (composite UNDER the robot)

```
[STYLE BIBLE]

SUBJECT: A single flat circular status halo ring for compositing beneath a
top-down robot sprite. A soft glowing annulus (donut), inner radius 55% of
frame, outer radius 95%, color STATUS_COLOR, opacity fading smoothly from
70% at the inner edge to 0% at the outer edge. Perfectly circular,
perfectly centered, nothing inside the inner radius (fully transparent hole).
No object, no robot, no icon — only the glow ring.

[NEGATIVE]
```

| State | STATUS_COLOR | Extra |
| --- | --- | --- |
| IDLE / available | `#34D399` | — |
| MOVING / path reserved | `#22D3EE` | — |
| YIELDING / waiting at conflict | `#F59E0B` | — |
| DEADLOCK / collision imminent | `#F43F5E` | `with a subtle double-ring pulse, two concentric bands` |
| CHARGING | `#E879F9` | — |

### 2.3 Payload / carried cargo (composite ON TOP of the robot)

```
[STYLE BIBLE]

SUBJECT: A single cardboard shipping tote seen from directly above, sized to
sit on a robot's load plate. Square with slightly rounded corners, cardboard
#C08A4E top face with a darker #8B5E34 rim showing the box wall thickness.
Two parallel packing-tape strips in a lighter tone running top-to-bottom.
A small flat blank white label rectangle in one corner (NO text, NO barcode
lines, NO characters — leave it blank, we overlay real IDs in code).

[NEGATIVE]
```

Variants: `a stack of two totes` · `an open empty tote showing dark interior` · `a wrapped pallet load in translucent grey stretch film`

### 2.4 Floor tiles — MUST be seamless

```
[STYLE BIBLE]

SUBJECT: A single seamlessly tileable square warehouse floor tile, viewed from
directly above. Polished concrete deck in #232A35 with a very subtle flat
panel seam in #2E3846 running along all four edges, exactly 4px wide,
half-width on each edge so that four copies tile perfectly with no visible seam.
Absolutely flat and even — no shading, no vignette, no wear, no highlight.
Opaque, fills the entire square frame edge to edge with NO margin and NO
transparent border.

CRITICAL: The pattern must be perfectly seamless and repeatable in a grid.
Left edge must match right edge exactly; top must match bottom exactly.

[NEGATIVE] + centered object, isolated object, transparent background, margin
```

Variants (keep the seamless clause each time):

1. **Drive aisle** — `two safety-yellow #F2C14E guidance lines, 8px wide, running vertically top to bottom, spaced 40% of the tile width apart.`
2. **Intersection / conflict zone** — `the yellow guidance lines cross in a plus shape, and a translucent amber #F59E0B diamond hatch fills the central crossing square at 20% opacity.`
3. **Pick station floor** — `a safety-yellow #F2C14E painted border rectangle inset 15% from all edges, with diagonal hazard stripes along the top edge only.`
4. **Charging pad** — `a magenta #E879F9 painted circle centered on the tile, with two flat metal contact strips in light steel #6B7889 across its center.`
5. **Blocked / obstruction** — `diagonal alert-red #F43F5E and dark slate hazard stripes at 45 degrees, at 35% opacity over the concrete.`
6. **Fiducial marker** — `a small dark square QR-style fiducial marker in the exact center, made of abstract non-readable square blocks, high contrast black on white, occupying 25% of the tile.`

### 2.5 Static warehouse furniture (top-down, transparent)

| Asset | Subject line |
| --- | --- |
| Storage rack 1×3 | `A warehouse pallet racking unit seen from directly above, three grid cells long and one cell deep. Steel-blue #4A5666 frame beams running the full length, with three bays separated by upright posts shown as small dark squares. Each bay holds a cardboard #C08A4E pallet load seen from the top. Vertical uprights read as darker rounded squares at each bay corner.` |
| Storage rack 1×5 | Same, `five bays`. |
| Pick / drop station | `A warehouse pick station work surface seen from directly above. A light steel #6B7889 rounded rectangular table with a flat inbox tray and outbox tray marked by a cyan #22D3EE and a green #34D399 painted rectangle respectively. A small dark scanner post at one corner.` |
| Charging dock | `A robot charging dock seen from directly above. A dark #333B47 wedge-shaped wall unit with two protruding light-steel #6B7889 contact prongs facing outward, and a single magenta #E879F9 status LED dot on its back plate.` |
| Conveyor segment | `A seamlessly tileable conveyor belt segment seen from directly above, running vertically. Dark #333B47 belt surface with evenly spaced light-steel #6B7889 horizontal roller lines, flanked by two steel-blue #4A5666 side rails. Tiles seamlessly top-to-bottom.` |
| Narrow choke point | `A pair of warehouse safety bollards seen from directly above — two flat circles in safety-yellow #F2C14E with a dark center dot, positioned near opposite edges of the frame with a clear gap between them.` |
| Obstacle: spill | `An irregular liquid spill puddle seen from directly above, flat organic blob shape in translucent cyan-grey, with a small folded yellow #F2C14E caution cone standing in its center seen from above as a yellow circle with concentric rings.` |
| **Obstacle: human worker** | `A warehouse worker seen from directly above — only the top of a safety-yellow #F2C14E high-visibility vest, dark shoulders, and a light hard-hat circle. Highly abstracted and flat, no facial features, no detail below the shoulders.` |
| Obstacle: dropped box | `A single tipped-over cardboard box lying on the floor seen from directly above, cardboard #C08A4E, with one flap open showing a dark interior, slightly rotated off-axis.` |

The **human worker** asset is not decoration — it is the non-communicating dynamic
obstacle that move #4 of [[sih-2026-ps26123-amr-fleet-critique]] requires in the demo.

---

## 3. Network / edge-compute visuals

These make decentralization legible on screen, which is the whole judged premise.

```
[STYLE BIBLE]

SUBJECT: A peer-to-peer mesh link connector graphic for overlaying between two
robots. A single straight horizontal beam of light in accent cyan #22D3EE:
a bright 3px core line with a soft outer glow, punctuated by five small
diamond-shaped data packets evenly spaced along its length, each slightly
brighter than the beam. Fully transparent above and below the beam.
Wide aspect ratio 4:1. No endpoints, no nodes, no arrowheads — the beam runs
edge to edge so it can be stretched between two points in code.

[NEGATIVE]
```

- **Broadcast pulse** — `A single expanding radio broadcast ring: three concentric thin circles in accent cyan #22D3EE, innermost at 100% opacity and outermost at 15%, perfectly centered, fully transparent inside. Nothing at the center.`
- **Intent reservation cone** — `A flat translucent forward-projection wedge showing a robot's claimed path: a trapezoid narrow at the bottom widening toward the top, filled with accent cyan #22D3EE at 25% opacity, with a brighter 2px outline and three faint chevron arrows pointing up along its length.`
- **Comms lost** — `A broken link symbol: a horizontal cyan beam that fades out and breaks in the middle, with a small alert-red #F43F5E X mark at the break point.`
- **Edge node badge** — `A flat top-down single-board computer (Raspberry Pi style) icon: a dark green-slate PCB rectangle with a black square SoC chip in the center, a row of small gold pin dots along one edge, two silver USB port rectangles, and one green #34D399 power LED dot.`
- **Deadlock detected** — `A flat warning glyph: two curved arrows in alert-red #F43F5E chasing each other in a circle (a cyclic-wait symbol), with a small exclamation triangle at the center.`

---

## 4. Dashboard UI kit

Batch these — flat monoline icons are the one case where grid generation works.

```
[STYLE BIBLE — but override OUTPUT to:]
OUTPUT: A single flat icon sheet on a transparent background, icons arranged
in a clean evenly spaced 4x2 grid with generous padding, all icons the same
optical weight and the same 2px stroke width, all monoline outline style,
all in accent cyan #22D3EE unless a color is specified.

SUBJECT: A set of 8 warehouse fleet-management dashboard icons, monoline
outline style, consistent stroke weight:
1. A battery, horizontal, drawn at full charge, in status green #34D399
2. The same battery at 20% charge, in amber #F59E0B
3. The same battery at 5% charge with a small alert bolt, in red #F43F5E
4. A lightning bolt inside a circle (charging)
5. A signal-strength bars icon, four ascending bars
6. A route icon: a dotted path winding between a start dot and an end pin
7. A stopwatch / timer
8. A package box in three-quarter outline

Every icon must sit inside the same invisible square bounding box and read
clearly at 24 pixels.

[NEGATIVE]
```

Second sheet:

```
SUBJECT: A set of 8 fleet-status dashboard icons, same monoline style:
1. A robot front-face glyph (rounded square with two eye dots)
2. A pause / yield glyph — two vertical bars inside a circle
3. A warning triangle with an exclamation mark, alert red #F43F5E
4. A checkmark inside a circle, status green #34D399
5. Two arrows crossing to form an X (conflict / collision)
6. A wrench (maintenance)
7. A cloud with a diagonal slash through it (offline / cloud-independent),
   in accent cyan
8. A hexagonal mesh of six connected nodes (peer-to-peer network)
```

Dashboard chrome:

```
[STYLE BIBLE]
SUBJECT: A seamlessly tileable dark dashboard background panel: base #1B2029
with a very faint #2E3846 blueprint grid of 40px squares, plus slightly
brighter major lines every 200px. Extremely subtle, low contrast, must sit
quietly behind data. Perfectly seamless on all four edges. Opaque, 16:9.
[NEGATIVE] + centered object, transparent background, bright, high contrast
```

---

## 5. Pitch-deck hero images (isometric — presentation only, never the sim)

```
STYLE: Clean modern isometric 3D illustration, 30-degree true isometric
projection, soft studio lighting, matte surfaces, subtle ambient occlusion,
same palette as the fleet UI (dark slate #1B2029 floor, off-white #E8EDF2
robots, accent cyan #22D3EE glow, safety yellow #F2C14E floor markings).
Premium tech-brand illustration quality, uncluttered, generous negative space.

SUBJECT: An isometric cutaway of a smart warehouse aisle intersection. Three
white autonomous mobile robots — one glowing cyan, one amber, one magenta
along their side light strips — approach a four-way junction marked with
safety-yellow floor lines. Glowing cyan beams of light connect each robot
directly to the other two, forming a triangle of peer-to-peer links above the
floor, with small diamond data packets travelling along the beams. Deliberately
NO server rack, NO cloud, NO wireless access point in the scene — the robots
are talking only to each other. Tall steel racking with cardboard pallets lines
both sides. Dark background, dramatic but clean.

NEGATIVE: text, letters, logos, watermark, people, clutter, photorealism,
rust, dirt, warm orange lighting, cables on floor, central server, router,
antenna tower, cloud icon
```

Further hero variants:

- **Deadlock resolved** — `two robots meeting head-on in a narrow single-width aisle; one has pulled into a side alcove glowing amber (yielding) while the other passes glowing cyan; a translucent cyan negotiation beam connects them`
- **Blocked-aisle reroute** — `one aisle blocked by a fallen pallet with red hazard glow; a translucent cyan dotted path arcs around through a parallel aisle; two robots follow the new route`
- **Edge vs cloud** — `split composition: on the left a dimmed, greyed-out cloud server with a broken red link and a stalled grey robot; on the right three brightly lit robots linked to each other in a cyan mesh, moving. Clear visual contrast between dead and alive.`

---

## 6. Production notes

1. **Generate one asset at a time**, not as sprite sheets. Grid instructions drift in
   spacing and scale. Batch only the flat UI icons (§4), where it works.
2. **Verify seamless tiles** by laying four copies in a 2×2 and checking the seams.
   If they fail, add `the pattern wraps around all edges, the right edge continues into
   the left edge` — or just draw the yellow lines in code, which is faster and pixel-perfect.
3. **Do not generate rotation states.** Generate facing-up once and rotate in the renderer.
   Eight generated rotations jitter as the robot turns.
4. **Status = overlay, not a new sprite.** 4 robots × 5 states = 20 drifting sprites;
   4 robots + 5 halo rings = 9 assets that composite perfectly. Same for battery, payload, comms.
5. **Tool flags**
   - Midjourney: `--style raw --ar 1:1 --v 7`, negatives via `--no`. Use `--sref` pointed at
     the first accepted robot to lock style across the set — highest-leverage single trick.
   - GPT-Image / DALL·E: ignores much of the negative prompt; phrase constraints positively
     ("the background is fully transparent"). Real alpha supported.
   - Flux / SD: alpha unreliable — use the `#FF00FF` fallback and key it. ControlNet on a
     hand-drawn rectangle gives exact chassis proportions.
6. **Naming convention** (decide before generating): `robot_amr01_base.png`,
   `halo_yield.png`, `tile_aisle_ns.png`, `rack_1x3.png`, `net_link_beam.png`, `ui_battery_low.png`.
7. **Budget**: ~35 distinct assets, realistically 100–120 generations with rerolls.
   Do the robot first; everything else is styled to match it.

## 7. Composite z-order (for the renderer)

```
floor tile → floor marking → reservation cone → status halo → robot →
payload → link beams → labels/HUD
```
