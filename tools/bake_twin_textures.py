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
from PIL import Image

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "frontend" / "assets" / "twin"
DEFAULT_SOURCE = pathlib.Path.home() / "Downloads" / "3D_assest_final" / "3D_assest_final"

# Source renders, by the only stable handle they have - their filename.
SRC = {
    "amr_yellow": "Gemini_Generated_Image_58n48c58n48c58n4.png",
    "amr_blue":   "Gemini_Generated_Image_ae4aqbae4aqbae4a.png",
    "floor_dark": "Gemini_Generated_Image_9ngfqc9ngfqc9ngf.png",
    "carton":     "Gemini_Generated_Image_j80itqj80itqj80i.png",
}


def perspective(im: Image.Image, quad, size: int) -> Image.Image:
    """Flatten a four-corner region (TL, TR, BR, BL) into a square."""
    dst = [(0, 0), (size, 0), (size, size), (0, size)]
    rows, rhs = [], []
    for (dx, dy), (sx, sy) in zip(dst, quad):
        rows.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy]); rhs.append(sx)
        rows.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy]); rhs.append(sy)
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

    # --- AMR deck ------------------------------------------------------------
    # The top-down view off the turnaround sheet, neutralised for tinting. This
    # goes on the +Y face of the chassis and nowhere else: it is the face the
    # orbit and tactical cameras actually see.
    amr = Image.open(source / SRC["amr_yellow"])
    deck = desaturate(key_background(amr, (1000, 112, 1742, 580)))
    deck.thumbnail((256, 256), Image.LANCZOS)
    made.append(save(deck, "amr_deck.png"))
    shots.append(("amr deck", deck))

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

    # --- worker ---------------------------------------------------------------
    # Deliberately NOT baked. The cut-out figure comes out clean, but the twin's
    # pedestrian is an articulated mesh that animates its stride, turns to its
    # heading, and (since cb72753) switches to a work pose at a station. A flat
    # sprite is a better still and a worse simulation: it would trade all three
    # behaviours for one nicer frame. Revisit if the figure is ever rebuilt as a
    # sprite sheet with those states in it.

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
