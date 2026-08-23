---
title: SIH26123 — asset loader spec (dimensions, anchors, z-order)
tags: [sih-2026, assets, spec]
type: reference
created: 2026-08-23
---

# SIH26123 — loader-side asset spec

Companion to `ps26123-sim-asset-prompts.md`. This is the contract the renderer holds the
generated assets to. Decide it before generating 100 images, not after.

## 1. Units

```
CELL_SRC  = 256   # px per grid cell in the source PNGs (authoring resolution)
CELL_PX   = 64    # px per grid cell on screen in the live sim
SCALE     = CELL_PX / CELL_SRC   # 0.25
```

Pre-scale every asset **once at load** into an offscreen surface at `CELL_PX` resolution.
Do not rescale per frame — with 4 robots × 8 layers × 60 fps the filtering cost is real, and
per-frame smoothing on rotated sprites shimmers.

- Canvas: `ctx.imageSmoothingQuality = 'high'` for the one-time downscale, then draw 1:1.
- Pygame: `pygame.transform.smoothscale` at load; `rotozoom` only for the rotating robot layer.

Keep the 256px originals. The demo video renders at `CELL_PX = 256` from the same manifest.

## 2. Anchor convention

An anchor is normalized `(ax, ay)` in `[0,1]` of the **source image**. To draw the asset so its
anchor lands on world point `p`:

```
top_left = p - (ax * w, ay * h)
```

Rotation is always about the anchor. Two anchors only:

| Anchor | Meaning | Used by |
| --- | --- | --- |
| `(0.0, 0.0)` | top-left of the asset's first grid cell | tiles, racks, stations, docks |
| `(0.5, 0.5)` | cell center / kinematic center | robots, halos, payloads, pulses |

Exceptions are listed explicitly in §3.

**Critical check at load time:** for every centered asset, compute the bounding box of
non-transparent pixels and assert its center is within 2px of the canvas center. Generators
routinely place the subject 5–15px off-center, which reads as a wobble when the robot rotates.
Re-center once at load rather than trying to re-roll the image.

## 3. Asset manifest

| Key | Source px | Anchor | Render size | Rotates? | Z |
| --- | --- | --- | --- | --- | --- |
| `tile_floor` | 256×256 | (0,0) | 1×1 cell | no | 0 |
| `tile_aisle_ns` / `_ew` | 256×256 | (0,0) | 1×1 | no | 0 |
| `tile_intersection` | 256×256 | (0,0) | 1×1 | no | 0 |
| `tile_pick_station` | 256×256 | (0,0) | 1×1 | no | 0 |
| `tile_charge_pad` | 256×256 | (0,0) | 1×1 | no | 0 |
| `tile_blocked` | 256×256 | (0,0) | 1×1 | no | 10 |
| `tile_fiducial` | 256×256 | (0,0) | 1×1 | no | 10 |
| `rack_1x3` | 768×256 | (0,0) | 3×1 | 0/90° only | 20 |
| `rack_1x5` | 1280×256 | (0,0) | 5×1 | 0/90° only | 20 |
| `station_pick` | 256×256 | (0,0) | 1×1 | 0/90/180/270 | 20 |
| `dock_charge` | 256×256 | (0,0) | 1×1 | 0/90/180/270 | 20 |
| `conveyor_seg` | 256×256 | (0,0) | 1×1 | 0/90° only | 20 |
| `obstacle_bollards` | 256×256 | (0,0) | 1×1 | 0/90° | 20 |
| `obstacle_spill` | 256×256 | (0.5,0.5) | 1.0× cell | no | 15 |
| `obstacle_box` | 256×256 | (0.5,0.5) | 0.7× cell | free | 25 |
| `agent_human` | 256×256 | (0.5,0.5) | 0.9× cell | **free, follows heading** | 50 |
| `net_cone_intent` | 256×512 | **(0.5, 1.0)** | w 1.0× cell, h = horizon × cell | yes, robot heading | 30 |
| `net_link_beam` | 512×128 | **(0.0, 0.5)** | w = ‖p₂−p₁‖, h 0.25× cell | yes, `atan2(Δy,Δx)` | 35 |
| `net_pulse` | 256×256 | (0.5,0.5) | animated 0.5→2.5× cell | no | 35 |
| `net_link_broken` | 512×128 | (0.0,0.5) | as beam | as beam | 36 |
| `halo_idle/moving/yield/deadlock/charging` | 256×256 | (0.5,0.5) | **1.3× cell** | **no** | 40 |
| `robot_amr01..04_base` | 256×256 | (0.5,0.5) | 1.0× cell | **yes, heading** | 50 |
| `payload_tote` / `_stack2` / `_wrapped` | 256×256 | (0.5,0.5) | 0.55× cell | **yes, with robot** | 60 |
| `glyph_deadlock` | 256×256 | (0.5,0.5) | 0.6× cell | no | 80 |
| `ui_icons_fleet` (sheet) | 1024×512 | per-cell (0.5,0.5) | 24px | no | HUD |
| `ui_icons_status` (sheet) | 1024×512 | per-cell (0.5,0.5) | 24px | no | HUD |
| `ui_bg_grid` | 1920×1080 | (0,0) | tiled | no | −10 |

Icon sheets are 4×2 grids of 256×256 cells → source rect `(col*256, row*256, 256, 256)`.

## 4. Z-order

```
-10  dashboard background grid
  0  floor tiles
 10  floor decals (blocked hatching, fiducials)
 15  flat ground obstacles (spill)
 20  static furniture (racks, stations, docks, conveyors, bollards)
 25  loose 3D obstacles (dropped box)
 30  intent / reservation cones
 35  P2P link beams, broadcast pulses
 40  status halos
 50  robots, human agents
 60  carried payloads
 80  alert glyphs (deadlock, comms lost)
 90  labels, IDs, battery bars
```

Revising one line from the prompt pack: **link beams go at 35, below the robots, not above.**
A beam drawn over the chassis cuts the robot in half visually; drawn underneath it appears to
plug into the robot and reads as connection rather than occlusion. The pack's z-list is
superseded by this table.

Within a z-band, sort by world `y` so lower-on-screen objects draw last. Cheap and it makes
racks and robots overlap correctly at the boundary.

## 5. Rotation rules

- **Robot + payload rotate together.** Compose them into one offscreen surface (robot, then
  payload at 0.55× centered), then rotate the composite once. Rotating two surfaces separately
  and hoping the centers agree produces visible drift at odd angles.
- **Halos never rotate.** They are radially symmetric; rotating them wastes a transform and,
  with smoothing on, makes them breathe.
- **Racks and furniture snap to 90° multiples only.** Free rotation on a 3-cell rack breaks the
  cell alignment the planner assumes.
- Sprite heading 0 = facing **+Y (up / north)**. Convert from a math-convention heading with
  `sprite_angle = degrees(theta) - 90` (Canvas/Pygame rotate clockwise-positive on screen; check
  the sign once with a single robot at heading 0 before trusting the whole fleet).

## 6. Manifest format

```json
{
  "cell_src": 256,
  "cell_px": 64,
  "assets": {
    "robot_amr01_base": {
      "file": "robot/robot_amr01_base.png",
      "anchor": [0.5, 0.5], "size": 1.0, "rotates": true, "z": 50
    },
    "halo_yield": {
      "file": "overlay/halo_yield.png",
      "anchor": [0.5, 0.5], "size": 1.3, "rotates": false, "z": 40
    },
    "net_link_beam": {
      "file": "net/net_link_beam.png",
      "anchor": [0.0, 0.5], "size": [null, 0.25], "rotates": "vector", "z": 35
    }
  }
}
```

`size` is in cell units; `null` means "stretched to a runtime-computed length".
Everything the renderer needs comes from this file — no per-asset special cases in draw code.

## 7. Draw loop shape

```
for layer in sorted(z_bands):
    for item in sorted(scene[layer], key=lambda i: i.world_y):
        blit(item.asset, item.pos, item.angle)
```

## 8. Directory layout

```
assets/
  tile/    tile_floor.png  tile_aisle_ns.png  ...
  robot/   robot_amr01_base.png ...
  overlay/ halo_idle.png  halo_yield.png ...
  payload/ payload_tote.png ...
  static/  rack_1x3.png  dock_charge.png ...
  net/     net_link_beam.png  net_cone_intent.png ...
  ui/      ui_icons_fleet.png  ui_bg_grid.png
  manifest.json
```

## 9. Acceptance checks before an asset is accepted into the set

1. Non-transparent bounding box is centered (centered assets) or flush to the top-left cell
   boundary (tiles and furniture).
2. Tiles: 2×2 copy test shows no seam.
3. Palette: sample 10 pixels, every one within ΔE 5 of a bible color. Off-palette assets are the
   main cause of a set that "looks generated".
4. Downscale to 64px and view at 100% — if it turns to mush, the asset carries too much detail
   for the render size and needs simplifying, not sharpening.
