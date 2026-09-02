"""Bake the 3D twin's texture set out of the asset render drop.

The drop is 45 presentation renders - 7 MB each, object on a plinth with an
engraved label, and *no transparency at all*: what looks like a cut-out
checkerboard is a checkerboard painted into the colour channels at alpha 255.
So nothing is usable as-is; every texture here is cut, keyed, corrected and
shrunk out of a render that was never meant to be one.

Run from the repo root:

    python tools/bake_twin_textures.py [--source DIR]

Writes frontend/assets/twin/. Opaque maps go out as JPEG and cut-outs as PNG,
because the demo ships vendored and has to survive venue Wi-Fi - the whole
source set is 324 MB and the baked set is under 200 KB.

Every crop box below was measured against a specific source file. If the drop is
ever replaced, the boxes are wrong and the previews this writes are how you find
out: pass --preview and look at tools/_texture_preview.png before trusting
anything.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
from PIL import Image, ImageDraw

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "frontend" / "assets" / "twin"
DEFAULT_SOURCE = pathlib.Path.home() / "Downloads" / "3D_assest_final" / "3D_assest_final"

# Source renders, by the only stable handle they have - their filename.
SRC = {
    "floor_dark": "Gemini_Generated_Image_9ngfqc9ngfqc9ngf.png",
    "carton":     "Gemini_Generated_Image_j80itqj80itqj80i.png",
    "worker_hi":  "Gemini_Generated_Image_o8fl7eo8fl7eo8fl.png",   # yellow vest
    "worker_org": "Gemini_Generated_Image_3clkrt3clkrt3clk.png",   # orange vest
    "worker_blu": "Gemini_Generated_Image_5t5wvb5t5wvb5t5w.png",   # blue vest
    "badge_charge":   "Gemini_Generated_Image_elbqyxelbqyxelbq.png",
    "badge_complete": "Gemini_Generated_Image_ms3vi0ms3vi0ms3v.png",
    "badge_deadlock": "Gemini_Generated_Image_ymvg7bymvg7bymvg.png",
}


def perspective(im: Image.Image, quad, size: int) -> Image.Image:
    """Flatten a four-corner region (TL, TR, BR, BL) into a square."""
    dst = [(0, 0), (size, 0), (size, size), (0, size)]
    rows, rhs = [], []
    for (dx, dy), (sx, sy) in zip(dst, quad):
        rows.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        rhs.append(sx)
        rows.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        rhs.append(sy)
    coeffs = np.linalg.lstsq(np.array(rows, float), np.array(rhs, float), rcond=None)[0]
    return im.transform((size, size), Image.PERSPECTIVE, coeffs, Image.BICUBIC)


def key_background(im: Image.Image, box, tolerance=95, sample=(8, 8)) -> Image.Image:
    """Cut a subject off the flat studio background these renders are shot on.

    The tolerance matters more than it looks: the AMR turnaround sheets have a
    faint blueprint grid drawn over the backdrop, and a tolerance tight enough to
    keep the robot's own shadow will also keep those grid lines as floating
    scratches. 95 is the value that drops the grid and holds the chassis.
    """
    rgb = im.convert("RGB")
    bg = np.asarray(rgb, dtype=np.int16)[sample[1], sample[0]].astype(int)
    sub = rgb.crop(box)
    a = np.asarray(sub, dtype=np.int16)
    dist = np.abs(a - bg).sum(axis=2)
    alpha = np.clip((dist - tolerance) * 6, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([np.asarray(sub, np.uint8), alpha]), "RGBA")


def trim(im: Image.Image) -> Image.Image:
    """Shrink to the non-transparent bounding box."""
    bb = im.getbbox()
    return im.crop(bb) if bb else im


def drop_plinth(im: Image.Image, fill=0.5) -> Image.Image:
    """Cut a standing figure off the plinth it was rendered on.

    Keying cannot do it - the plinth is a different grey from the backdrop, so it
    survives and rides along as a slab under the feet, which on a billboard reads
    as the worker standing on a doorstep.

    The proportions give it away. On the *untrimmed* crop the plinth spans almost
    the full width while the legs above it span a quarter of it, so scanning up
    from the last non-empty row, the first row narrower than half the crop is the
    ground line. Measuring against the crop rather than against the silhouette is
    what makes this work on every source: these renders are not one shape - the
    yellow worker is portrait and the blue one landscape, and a rule written
    against the figure's own width gets a different answer on each.
    """
    a = np.asarray(im.convert("RGBA"))
    solid = a[..., 3] > 40
    widths = solid.sum(axis=1)
    rows = np.nonzero(widths)[0]
    if not len(rows):
        return im
    limit = im.width * fill
    cut = rows[-1] + 1
    for y in range(rows[-1], rows[0], -1):
        if widths[y] < limit:
            cut = y + 1
            break
    return im.crop((0, 0, im.width, cut))


def fade_base(im: Image.Image, frac=0.09) -> Image.Image:
    """Ramp the bottom of a cut-out figure to transparent.

    drop_plinth removes the slab, but what is left underneath differs per render -
    a hard edge on one, a soft contact shadow tapering over a hundred rows on
    another - and no single width threshold gets all three. Dissolving the last
    few percent handles every case and reads as the figure meeting the floor
    rather than as a sticker sitting on it.
    """
    a = np.asarray(im.convert("RGBA")).astype(np.float32)
    h = a.shape[0]
    band = max(1, int(h * frac))
    ramp = np.linspace(0.0, 1.0, band, dtype=np.float32)
    a[h - band:, :, 3] *= ramp[:, None]
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def desaturate(im: Image.Image, floor=0.30, ceil=1.0) -> Image.Image:
    """Greyscale a cut-out so the twin can tint it per robot.

    The fleet already has ten identity colours that the roster, the 2D view and
    the route lines all agree on. Baking the render's own yellow in would either
    throw that away or need one texture per robot; a neutral map multiplied by
    the material colour keeps one texture and all ten identities.
    """
    rgba = np.asarray(im.convert("RGBA"), dtype=np.float32)
    lum = (rgba[..., 0] * .299 + rgba[..., 1] * .587 + rgba[..., 2] * .114) / 255.0
    lum = np.clip(floor + lum * (ceil - floor), 0, 1) * 255.0
    grey = np.dstack([lum, lum, lum, rgba[..., 3]]).astype(np.uint8)
    return Image.fromarray(grey, "RGBA")


def seamless(tile: Image.Image, size: int) -> Image.Image:
    """Four-way mirror, so the tile repeats without a visible seam."""
    w, h = tile.size
    out = Image.new("RGB", (w * 2, h * 2))
    out.paste(tile, (0, 0))
    out.paste(tile.transpose(Image.FLIP_LEFT_RIGHT), (w, 0))
    out.paste(tile.transpose(Image.FLIP_TOP_BOTTOM), (0, h))
    out.paste(tile.transpose(Image.ROTATE_180), (w, h))
    return out.resize((size, size), Image.LANCZOS)


def save(im: Image.Image, name: str) -> pathlib.Path:
    path = OUT / name
    if path.suffix == ".jpg":
        im.convert("RGB").save(path, "JPEG", quality=82, optimize=True)
    else:
        im.save(path, "PNG", optimize=True)
    return path


BUILDER_OUT = REPO / "frontend" / "assets" / "twin" / "builder"

def bake_builder_tiles(shots) -> list:
    """Top-down 96px tiles for the scenario builder palette and grid.

    Each one is composited from what the twin actually puts on that cell, so the
    palette and the 3D result cannot drift apart:

        floor    the warehouse floor texture itself
        rack     four cartons on a dark frame - a rack from above IS four cartons
        station  floor plus the blue corner brackets the 3D station inlays
        dock     floor plus the green alignment cross the 3D dock inlays
        amr      floor plus the existing top-down robot sprite
    """
    BUILDER_OUT.mkdir(parents=True, exist_ok=True)
    N = 96
    # A quadrant of the floor texture, away from its centre cross. The full tile
    # carries a lane marking, and the twin repeats that once per two cells across
    # the whole floor - one builder tile per cell would put a yellow line through
    # every square and the grid would read as a road map.
    floor_src = Image.open(OUT / "floor_panel.jpg").convert("RGB")
    q = floor_src.width // 2
    floor = floor_src.crop((14, 14, q - 14, q - 14)).resize((N, N), Image.LANCZOS)
    carton = Image.open(OUT / "carton.jpg").convert("RGB")

    def out(im, name):
        path = BUILDER_OUT / name
        im.convert("RGBA").save(path, "PNG", optimize=True)
        shots.append((name, im.convert("RGBA")))
        return path

    made = [out(floor, "tile_floor.png")]

    # rack: dark deck, four cartons in the same quadrants the twin uses
    rack = Image.new("RGB", (N, N), (26, 34, 46))
    chip = carton.resize((int(N * .38), int(N * .38)), Image.LANCZOS)
    for qx in (0.28, 0.72):
        for qy in (0.28, 0.72):
            rack.paste(chip, (int(qx * N - chip.width / 2), int(qy * N - chip.height / 2)))
    d = ImageDraw.Draw(rack)
    d.rectangle([0, 0, N - 1, N - 1], outline=(58, 84, 112), width=3)
    made.append(out(rack, "tile_rack.png"))

    # station: the 3D station's corner brackets, same blue
    station = floor.copy()
    d = ImageDraw.Draw(station)
    blue, arm, w = (59, 130, 246), int(N * .26), 5
    for cx, cy, dx, dy in ((10, 10, 1, 1), (N - 11, 10, -1, 1), (10, N - 11, 1, -1), (N - 11, N - 11, -1, -1)):
        d.line([cx, cy, cx + dx * arm, cy], fill=blue, width=w)
        d.line([cx, cy, cx, cy + dy * arm], fill=blue, width=w)
    made.append(out(station, "tile_station.png"))

    # dock: the 3D dock's alignment cross, same green
    dock = floor.copy()
    d = ImageDraw.Draw(dock)
    green = (70, 211, 154)
    d.rectangle([N * .5 - 4, N * .16, N * .5 + 4, N * .84], fill=green)
    d.rectangle([N * .16, N * .5 - 4, N * .84, N * .5 + 4], fill=green)
    d.rectangle([N * .5 - 11, N * .5 - 11, N * .5 + 11, N * .5 + 11], fill=green)
    made.append(out(dock, "tile_amr_dock.png"))

    # amr: the existing top-down robot sprite over floor
    amr = floor.copy().convert("RGBA")
    robot = Image.open(REPO / "frontend" / "assets" / "robots" / "robot_amr01_base.png").convert("RGBA")
    robot.thumbnail((int(N * .82), int(N * .82)), Image.LANCZOS)
    amr.paste(robot, ((N - robot.width) // 2, (N - robot.height) // 2), robot)
    made.append(out(amr, "tile_amr.png"))

    # human: the existing top-down worker sprite over floor
    human = floor.copy().convert("RGBA")
    worker = Image.open(REPO / "frontend" / "assets" / "misc" / "furniture" / "worker_human.png").convert("RGBA")
    worker.thumbnail((int(N * .74), int(N * .74)), Image.LANCZOS)
    human.paste(worker, ((N - worker.width) // 2, (N - worker.height) // 2), worker)
    made.append(out(human, "tile_human.png"))
    return made


def bake(source: pathlib.Path, preview: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    missing = [n for n, f in SRC.items() if not (source / f).exists()]
    if missing:
        sys.exit(f"missing source renders for: {', '.join(missing)}\n  looked in {source}")

    made, shots = [], []

    # --- warehouse floor -----------------------------------------------------
    # The "Floor Dark" badge holds a near face-on tile inside its bezel. Flatten
    # it, then take a section from inside the yellow marking rectangle: panels
    # and seams, with just enough of the marking caught at the edge that the
    # repeat reads as lane striping rather than as wallpaper.
    fd = Image.open(source / SRC["floor_dark"]).convert("RGB")
    flat = perspective(fd, [(447, 250), (1385, 322), (1385, 1274), (447, 1387)], 512)
    panel = flat.crop((148, 150, 392, 394)).resize((256, 256), Image.LANCZOS)
    made.append(save(seamless(panel, 512), "floor_panel.jpg"))
    shots.append(("floor", seamless(panel, 512)))

    # --- AMR deck -------------------------------------------------------------
    # Deliberately NOT baked any more. The top-down view was mapped onto the
    # chassis and was the least convincing thing in the scene: it depicts a robot
    # with a mast and gantry that the twin's chassis does not have, so the outline
    # lined up with nothing under it, and a 1.59:1 source stretched onto a nearly
    # square face on top of that.
    #
    # The distinction worth keeping from this: a MATERIAL texture transfers out of
    # these renders - the floor panelling and the corrugated card describe a
    # surface, and a surface is a surface. A DEPICTION does not: it describes an
    # object, and the object in the render is not the object in the scene. The
    # AMR turnarounds remain excellent reference for *modelling* a robot; they are
    # not a texture for one.

    # --- cargo carton --------------------------------------------------------
    # A clean patch of corrugated card between the tape straps, mirrored into a
    # tile. The obvious crop - a whole box face - cannot work: the boxes are shot
    # three-quarter so every face is a parallelogram, and a rectangular crop of
    # one always takes a corner of the (painted) checkerboard with it.
    carton = Image.open(source / SRC["carton"]).convert("RGB")
    # Box found by search, not by eye: the largest 300px window that is entirely
    # warm-toned (r>g>b, mid brightness) and has the lowest variance, which is
    # what "flat clean card, no tape, no checkerboard" reduces to numerically.
    patch = carton.crop((1280, 1240, 1580, 1540)).resize((192, 192), Image.LANCZOS)
    made.append(save(seamless(patch, 256), "carton.jpg"))
    shots.append(("carton", seamless(patch, 256)))

    # --- workers --------------------------------------------------------------
    # Three vest colours, cut off their plinths. The figure sits in the left
    # portion of each render with detail insets down the right; the caption is
    # engraved on the plinth below it, so the crop stops above 0.75H.
    for key, name in (("worker_hi", "worker_hi.png"),
                      ("worker_org", "worker_orange.png"),
                      ("worker_blu", "worker_blue.png")):
        src = Image.open(source / SRC[key])
        w, h = src.size
        fig = fade_base(trim(drop_plinth(
            key_background(src, (int(w*.13), int(h*.04), int(w*.56), int(h*.78)), tolerance=60))))
        fig.thumbnail((128, 288), Image.LANCZOS)
        made.append(save(fig, name))
        shots.append((name, fig))

    # --- status badges ----------------------------------------------------------
    # The tile and its bezel, off the plinth. These become markers that float over
    # a robot, so the state a halo colour was carrying alone becomes readable at a
    # glance instead of needing the legend.
    for key, name in (("badge_charge", "badge_charging.png"),
                      ("badge_complete", "badge_complete.png"),
                      ("badge_deadlock", "badge_deadlock.png")):
        src = Image.open(source / SRC[key])
        w, h = src.size
        # 0.632H, not lower: the plinth caption is engraved directly beneath the
        # badge and a taller crop takes the top of the lettering with it.
        badge = trim(key_background(src, (int(w*.05), int(h*.055), int(w*.71), int(h*.645)),
                                    tolerance=70))
        badge.thumbnail((160, 160), Image.LANCZOS)
        made.append(save(badge, name))
        shots.append((name, badge))

    # --- worker ---------------------------------------------------------------
    # Deliberately NOT baked. The cut-out figure comes out clean, but the twin's
    # pedestrian is an articulated mesh that animates its stride, turns to its
    # heading, and (since cb72753) switches to a work pose at a station. A flat
    # sprite is a better still and a worse simulation: it would trade all three
    # behaviours for one nicer frame. Revisit if the figure is ever rebuilt as a
    # sprite sheet with those states in it.

    # --- scenario builder palette ---------------------------------------------
    # Baked from the same sources the twin renders from, so the tile you paint in
    # the builder is a picture of what you will actually get. The alternative -
    # hand-picked sprites, or flat colours - drifts the moment either side
    # changes, and a builder whose preview lies is worse than one with no preview.
    made += bake_builder_tiles(shots)

    total = sum(p.stat().st_size for p in made)
    for p in made:
        print(f"  {p.name:20} {p.stat().st_size/1024:6.1f} KB")
    print(f"  {'TOTAL':20} {total/1024:6.1f} KB")

    if preview:
        pad, cell = 10, 260
        sheet = Image.new("RGB", (cell * len(shots) + pad, cell + 34), (26, 30, 38))
        for i, (name, im) in enumerate(shots):
            thumb = im.copy().convert("RGBA")
            thumb.thumbnail((cell - pad * 2, cell - pad * 2), Image.LANCZOS)
            sheet.paste(thumb, (i * cell + pad + (cell - pad*2 - thumb.width)//2, 30), thumb)
        shot = REPO / "tools" / "_texture_preview.png"
        sheet.save(shot)
        print(f"  preview -> {shot}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE)
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    bake(args.source, args.preview)
