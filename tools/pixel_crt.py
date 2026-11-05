#!/usr/bin/env python3
"""Pixel filter (black, grey and white, detailed).

Turns every photo in a folder into an image made of individual square pixels. Your originals are only read, never changed; the
pixelated copies go into a separate folder with the same file names (as .png).

Usage:
    python3 pixel_crt.py <album folder> <output folder> [--cells 96] [--cell-size 15]

  --cells      pixels across the short side of the picture (96 = the finer version you
               picked; 72 = chunkier, larger = more detail)
  --cell-size  size of each pixel in the output image, in real pixels (15 -> 1440 px
               on the short side)
  --style      how the pixels are drawn:
                 border  (default) every pixel a touch smaller, leaving a hairline gap
                 rounded full-size pixels with slightly rounded corners
                 jitter  each pixel very slightly smaller or larger than the next
                 solid   pixels fill their whole cell; same-colour pixels merge
                 stitch  every pixel is a cross-stitch X on black, like the project logo
                         (black pixels are left empty, so the black shows through)
                 beads   every pixel is a round fused bead with a hole (like Perler beads),
                         on black; black pixels are left empty
                 melted  beads, with about a third of them slightly melted from the iron
                         (a bit wider and wobblier, smaller off-centre hole); the same
                         beads melt every run
  --grey       third tone between black and white: "dark" (default, dark grey),
               "light" (very light grey) or "none" (black and white only)
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

try:  # iPhone .heic photos, if the optional pillow-heif package is installed
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
BACKGROUND = 38  # dark grey seen between pixels
SUPERSAMPLE = 4  # pixels are drawn 4x larger, then scaled down, so every edge is even
STYLES = ("border", "rounded", "jitter", "solid", "stitch", "beads", "melted")
GREYS = {"none": None, "light": 200, "dark": 96}


def pixelate(path, cells, cell_size, grey=None, style="border"):
    im = ImageOps.exif_transpose(Image.open(path)).convert("L")  # respect phone rotation
    w, h = im.size
    if w <= h:
        cols, rows = cells, max(1, round(h * cells / w))
    else:
        rows, cols = cells, max(1, round(w * cells / h))

    # Boost local contrast so faces keep their features in two tones.
    eq = ImageOps.equalize(im)
    g = Image.blend(ImageOps.autocontrast(im, cutoff=1), eq, 0.6)
    g = g.filter(ImageFilter.UnsharpMask(6, 150))
    small = np.asarray(g.resize((cols, rows), Image.BOX), dtype=np.float32) / 255.0

    bayer = np.array([[0, 2], [3, 1]], dtype=np.float32) / 4.0 + 0.125
    pattern = np.tile(bayer, (rows // 2 + 1, cols // 2 + 1))[:rows, :cols]
    if grey is None:
        # Two tones: light checkerboard dither in the mid-tones; darks and lights stay solid.
        threshold = 0.35 + 0.30 * pattern
        bw = np.where(small > threshold, 255, 0).astype(np.uint8)
    else:
        # Three tones: black, one grey, white. The mid-tones become solid grey (easier to
        # read than a checkerboard), with a light dither only right at the edges between tones.
        low = 0.38 + 0.08 * (pattern - 0.5)  # higher = more of the shadows go black
        high = 0.66 + 0.08 * (pattern - 0.5)
        q = np.where(small > high, 2, np.where(small > low, 1, 0)).astype(np.uint8)
        # Tidy up: a lone pixel that matches none of its 8 neighbours takes the most
        # common tone around it (removes speckle in busy areas, keeps real details).
        p = np.pad(q, 1, mode="edge")
        nb = np.stack([p[1 + dy:1 + dy + rows, 1 + dx:1 + dx + cols]
                       for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx])
        lone = (nb != q).all(0)
        majority = np.stack([(nb == t).sum(0) for t in range(3)]).argmax(0)
        q = np.where(lone, majority, q).astype(np.uint8)
        bw = np.array([0, grey, 255], np.uint8)[q]

    return draw_pixels(bw, cell_size, style)


def draw_pixels(tones, cell_size, style):
    """Draws one square per cell. All sizes are fractions of a cell, so the look is the
    same at any --cell-size; drawing big and scaling down keeps every gap the same width."""
    rows, cols = tones.shape
    if style == "melted":
        return draw_melted(tones, cell_size)
    if style in ("stitch", "beads"):
        return draw_stamps(tones, cell_size, style)
    if style == "solid":
        return Image.fromarray(tones).convert("RGB").resize((cols * cell_size, rows * cell_size),
                                                           Image.NEAREST)
    c = cell_size * SUPERSAMPLE
    canvas = Image.new("L", (cols * c, rows * c), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    inset, radius = {"border": (0.045, 0), "rounded": (0.0, 0.2), "jitter": (0.03, 0)}[style]
    rng = np.random.default_rng(7)  # same picture every run
    jitter = rng.uniform(-0.05, 0.05, (rows, cols)) if style == "jitter" else np.zeros((rows, cols))
    for y in range(rows):
        for x in range(cols):
            m = (inset + jitter[y, x]) * c
            box = [x * c + m, y * c + m, (x + 1) * c - m - 1, (y + 1) * c - m - 1]
            fill = int(tones[y, x])
            if radius:
                draw.rounded_rectangle(box, radius=radius * c, fill=fill)
            else:
                draw.rectangle(box, fill=fill)
    return canvas.resize((cols * cell_size, rows * cell_size), Image.LANCZOS).convert("RGB")


def stamp_for(style, c):
    """The shape drawn in one c x c cell, as a True/False pixel mask."""
    yy, xx = np.mgrid[0:c, 0:c]
    if style == "beads":
        # A round bead with a hole in the middle, a hair smaller than the cell.
        centre = (c - 1) / 2
        d = np.hypot(xx - centre, yy - centre)
        return (d <= c / 2 - 0.35) & (d >= c * 0.17)
    # Cross stitch, in the logo's proportions: a 24 px cell has a 1 px margin and 9 px arms.
    m, k = round(c / 24), max(0, round(c * 4 / 24))
    inside = (xx >= m) & (xx < c - m) & (yy >= m) & (yy < c - m)
    return inside & ((np.abs(xx - yy) <= k) | (np.abs(xx + yy - (c - 1)) <= k))


def draw_stamps(tones, cell_size, style):
    """One stitch or bead per cell on black, drawn at the final size with no scaling, so every
    one is pixel-exact and identical."""
    rows, cols = tones.shape
    c = cell_size
    # Paint each cell's tone over its shape: tile the tones up to full size, then keep only
    # the shape's pixels.
    full = np.kron(tones, np.ones((c, c), np.uint8))
    mask = np.tile(stamp_for(style, c), (rows, cols))
    return Image.fromarray(np.where(mask, full, 0).astype(np.uint8)).convert("RGB")


def draw_melted(tones, c, share=0.35, seed=7):
    """Beads, with a random share of them slightly melted: a bit wider and wobblier, with a
    smaller, off-centre hole. Drawn at the final size with hard pixel edges (no smoothing).
    Melted beads can spread just past their cell, so they're drawn last, on top of their
    neighbours."""
    rows, cols = tones.shape
    rng = np.random.default_rng(seed)  # same picture every run
    melt = np.where(rng.random((rows, cols)) < share, rng.uniform(0.3, 1.0, (rows, cols)), 0.0)
    pad = 2  # room for a melted bead to spread past its cell
    out = np.zeros((rows * c + 2 * pad, cols * c + 2 * pad), np.uint8)
    n = c + 2 * pad
    yy, xx = np.mgrid[0:n, 0:n]
    centre = (n - 1) / 2
    dx, dy = xx - centre, yy - centre
    dist, angle = np.hypot(dx, dy), np.arctan2(dy, dx)
    plain = (dist <= c / 2 - 0.35) & (dist >= c * 0.17)  # same bead as --style beads
    for m, r, k in sorted((melt[r, k], r, k) for r in range(rows) for k in range(cols)):
        tone = tones[r, k]
        if tone == 0:
            continue
        if m == 0:
            shape = plain
        else:
            p1, p2 = rng.uniform(0, 2 * np.pi, 2)
            wobble = 1 + m * (0.07 * np.sin(2 * angle + p1) + 0.05 * np.sin(3 * angle + p2))
            outer = (c / 2 - 0.35 + m * 0.9) * wobble
            hx, hy = rng.uniform(-1, 1, 2) * m * c * 0.06
            hole = c * 0.17 * (1 - 0.45 * m)
            shape = (dist <= outer) & (np.hypot(dx - hx, dy - hy) >= hole)
        out[r * c:r * c + n, k * c:k * c + n][shape] = tone
    return Image.fromarray(out[pad:pad + rows * c, pad:pad + cols * c]).convert("RGB")


def main():
    ap = argparse.ArgumentParser(description="Pixel-grid CRT filter for a folder of photos")
    ap.add_argument("album", type=Path, help="folder with the original photos")
    ap.add_argument("output", type=Path, help="folder for the pixelated copies")
    ap.add_argument("--cells", type=int, default=96)
    ap.add_argument("--cell-size", type=int, default=15)
    ap.add_argument("--style", choices=STYLES, default="border",
                    help="border (default), rounded, jitter, solid, stitch, beads or melted")
    ap.add_argument("--grey", choices=["none", "light", "dark"], default="dark",
                    help="third tone: dark grey (default), light grey, or none for black and white only")
    args = ap.parse_args()

    if not args.album.is_dir():
        sys.exit(f"Album folder not found: {args.album}")
    if args.output.resolve() == args.album.resolve():
        sys.exit("Use a different output folder so your originals stay untouched.")
    args.output.mkdir(parents=True, exist_ok=True)

    photos = sorted(p for p in args.album.iterdir() if p.suffix.lower() in EXTENSIONS)
    if not photos:
        sys.exit(f"No photos found in {args.album}")
    done = failed = 0
    for i, photo in enumerate(photos, 1):
        target = args.output / (photo.stem + ".png")
        try:
            pixelate(photo, args.cells, args.cell_size, GREYS[args.grey], args.style).save(target)
            done += 1
            print(f"[{i}/{len(photos)}] {photo.name} -> {target.name}")
        except Exception as e:  # keep going if one file is unreadable
            failed += 1
            print(f"[{i}/{len(photos)}] skipped {photo.name}: {e}")
    print(f"\nDone: {done} pixelated into {args.output}" + (f", {failed} skipped" if failed else ""))


if __name__ == "__main__":
    main()
