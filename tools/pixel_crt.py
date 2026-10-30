#!/usr/bin/env python3
"""Pixel-grid CRT filter (black-and-white, detailed).

Turns every photo in a folder into a two-tone pixel image with a dark grid between
the "pixels", like an old screen. Your originals are only read, never changed; the
pixelated copies go into a separate folder with the same file names (as .png).

Usage:
    python3 pixel_crt.py <album folder> <output folder> [--cells 72] [--cell-size 20]

  --cells      pixels across the short side of the picture (72 = the version you picked;
               smaller = chunkier, larger = more detail)
  --cell-size  size of each pixel in the output image, in real pixels (20 -> 1440 px
               on the short side)
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
GRID_COLOR = (38, 38, 38)


def pixelate(path, cells, cell_size):
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

    # Light checkerboard dither in the mid-tones only; darks and lights stay solid.
    bayer = np.array([[0, 2], [3, 1]], dtype=np.float32) / 4.0 + 0.125
    threshold = 0.35 + 0.30 * np.tile(bayer, (rows // 2 + 1, cols // 2 + 1))[:rows, :cols]
    bw = np.where(small > threshold, 255, 0).astype(np.uint8)

    big = Image.fromarray(bw).convert("RGB").resize((cols * cell_size, rows * cell_size),
                                                    Image.NEAREST)
    line = max(1, cell_size // 8)
    draw = ImageDraw.Draw(big)
    for x in range(0, cols * cell_size, cell_size):
        draw.rectangle([x, 0, x + line - 1, rows * cell_size], fill=GRID_COLOR)
    for y in range(0, rows * cell_size, cell_size):
        draw.rectangle([0, y, cols * cell_size, y + line - 1], fill=GRID_COLOR)
    return big


def main():
    ap = argparse.ArgumentParser(description="Pixel-grid CRT filter for a folder of photos")
    ap.add_argument("album", type=Path, help="folder with the original photos")
    ap.add_argument("output", type=Path, help="folder for the pixelated copies")
    ap.add_argument("--cells", type=int, default=72)
    ap.add_argument("--cell-size", type=int, default=20)
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
            pixelate(photo, args.cells, args.cell_size).save(target)
            done += 1
            print(f"[{i}/{len(photos)}] {photo.name} -> {target.name}")
        except Exception as e:  # keep going if one file is unreadable
            failed += 1
            print(f"[{i}/{len(photos)}] skipped {photo.name}: {e}")
    print(f"\nDone: {done} pixelated into {args.output}" + (f", {failed} skipped" if failed else ""))


if __name__ == "__main__":
    main()
