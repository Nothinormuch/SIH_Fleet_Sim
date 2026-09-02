"""Bake the team logo into a real cut-out for the boot screen.

The logo arrives as a JPEG on a black ground - the shape it is always shared in,
and the shape that is useless over anything that is not black. The boot screen
lays it over a lit navy gradient, so a black rectangle would show its own seam.

The conversion is the one an emissive-on-black image asks for: the logo is
already premultiplied against black, so its own brightness *is* its coverage.

    alpha = max(R, G, B)          value, not luma, so the green circuit traces
                                  and the cyan tagline key at full strength
                                  instead of dropping out at 30% the way a
                                  luma-weighted mask would take them
    RGB   = RGB / alpha           unpremultiply, or every soft edge comes out
                                  muddy and desaturated once a browser
                                  composites it a second time

A noise floor is subtracted first. JPEG puts ringing in the 1-8/255 range across
the whole black field, and left in it becomes a faint grey haze over the entire
frame - visible on a projector, invisible on a laptop.

Run from the repo root:

    python tools/bake_brand_assets.py [--verify]

Writes frontend/assets/brand/bios-logo.png.

--verify measures the ALPHA CHANNEL rather than looking at the picture, which is
the only check that separates a real cut-out from an opaque image of one. A
generated "transparent" asset routinely paints the checkerboard into the colour
channels at alpha 255 and survives every casual look; this repo has been bitten
by exactly that. If the transparent fraction reads ~0.00 the bake did nothing.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
from PIL import Image

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAND = REPO / "frontend" / "assets" / "brand"
SOURCE = BRAND / "bios-logo-source.jpg"
OUTPUT = BRAND / "bios-logo.png"

# Below this, a pixel is JPEG ringing rather than artwork. Measured on the
# source: 83% of it sits at or under 8/255, and the brightest thing that is
# unambiguously background noise is 8.
NOISE_FLOOR = 9.0 / 255.0

# Kept when trimming, in pixels of the source. The circuit traces run within a
# few pixels of the frame edge, so the trim is almost a no-op by design - it
# exists to stop a future re-export with different padding from silently
# changing where the logo sits on the boot screen.
TRIM_MARGIN = 2


def cutout(source: pathlib.Path) -> Image.Image:
    rgb = np.asarray(Image.open(source).convert("RGB"), dtype=np.float32) / 255.0

    alpha = rgb.max(axis=2)
    alpha = np.clip((alpha - NOISE_FLOOR) / (1.0 - NOISE_FLOOR), 0.0, 1.0)

    # Unpremultiply. Guard the divide: where alpha is 0 the colour is unused, and
    # 0/0 would seed NaNs that PIL turns into confetti.
    safe = np.maximum(alpha, 1e-4)[:, :, None]
    straight = np.clip(rgb / safe, 0.0, 1.0)

    out = np.concatenate([straight, alpha[:, :, None]], axis=2)
    return Image.fromarray((out * 255.0 + 0.5).astype(np.uint8), mode="RGBA")


def trim(image: Image.Image, margin: int = TRIM_MARGIN) -> Image.Image:
    """Crop to the artwork's own bounding box so layout does not inherit padding."""
    box = image.getchannel("A").getbbox()
    if box is None:
        return image
    left, top, right, bottom = box
    return image.crop((
        max(0, left - margin),
        max(0, top - margin),
        min(image.width, right + margin),
        min(image.height, bottom + margin),
    ))


def verify(image: Image.Image) -> bool:
    """Report on the alpha channel. Returns False if this is not a real cut-out."""
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    clear = float((alpha == 0).mean())
    solid = float((alpha == 255).mean())
    corners = [int(alpha[y, x]) for y, x in
               [(0, 0), (0, -1), (-1, 0), (-1, -1)]]

    print(f"  size                {image.width} x {image.height}")
    print(f"  fully transparent   {clear:.3f}")
    print(f"  fully opaque        {solid:.3f}")
    print(f"  corner alphas       {corners}")

    ok = clear > 0.5 and all(c == 0 for c in corners)
    print("  verdict             " + ("real cut-out" if ok else
                                      "NOT A CUT-OUT - the ground is still painted in"))
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=pathlib.Path, default=SOURCE)
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true",
                        help="measure the alpha channel of the result")
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"source not found: {args.source}", file=sys.stderr)
        return 1

    logo = trim(cutout(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logo.save(args.output, optimize=True)
    print(f"wrote {args.output.relative_to(REPO)}  ({args.output.stat().st_size / 1024:.1f} KB)")

    if args.verify:
        return 0 if verify(logo) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
