"""Rebuilds every project logo asset from the owner's dot-grid logo (43 x 40 dots, 7 colours).

Usage: python3 tools/logo/make_logo.py assets/logo.png <repo root> <folder for grid.txt>
"""
import sys
from PIL import Image, ImageDraw
import numpy as np

SRC, REPO, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
PALETTE = {  # char: colour, exactly as in the source image
    'r': (192, 0, 6), 'R': (92, 3, 4), 'g': (140, 140, 139), 'G': (93, 94, 89),
    'd': (44, 44, 38), 'w': (252, 252, 252), 'l': (204, 204, 202)}
im = np.array(Image.open(SRC).convert('RGB')).astype(int)
X0, Y0, P, COLS, ROWS = 218, 242, 12, 43, 40
lookup = {v: k for k, v in PALETTE.items()}
grid = []
for r in range(ROWS):
    row = ''
    for c in range(COLS):
        blk = im[Y0 + r * P:Y0 + r * P + P, X0 + c * P:X0 + c * P + P]
        l = blk.sum(2)
        px = tuple(int(v) for v in blk[np.unravel_index(l.argmax(), l.shape)])
        row += lookup[px] if sum(px) > 30 else '.'
    grid.append(row)

def render(pitch, dot, scale=1.0, size=None, ss=4, bg=(0, 0, 0, 255)):
    """Round dots, supersampled so small sizes stay round; returns RGBA."""
    w, h = COLS * pitch, ROWS * pitch
    big = Image.new('RGBA', (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    off = (pitch - dot) / 2
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch == '.': continue
            col = tuple(min(255, int(v * scale)) for v in PALETTE[ch]) + (255,)
            x, y = (c * pitch + off) * ss, (r * pitch + off) * ss
            d.ellipse([x, y, x + dot * ss - 1, y + dot * ss - 1], fill=col)
    img = big.resize((w, h), Image.LANCZOS)
    return img

def on_square(logo, side, fill_frac, bg, dy=0):
    canvas = Image.new('RGBA', (side, side), bg)
    lw = round(side * fill_frac)
    lh = round(lw * logo.height / logo.width)
    lg = logo.resize((lw, lh), Image.LANCZOS)
    canvas.alpha_composite(lg, ((side - lw) // 2, (side - lh) // 2 + dy))
    return canvas

hi = render(48, 32)  # master, same proportions as the source (dot 8 / pitch 12)
# README icon: round (black disc, transparent outside), logo sized to fit inside the circle
readme = on_square(hi, 384 * 4, 0.70, (0, 0, 0, 255), dy=10 * 4)  # 10 px lower: looks balanced (owner)
disc = Image.new('L', readme.size, 0)
ImageDraw.Draw(disc).ellipse([0, 0, readme.width - 1, readme.height - 1], fill=255)
readme.putalpha(disc)
readme = readme.resize((384, 384), Image.LANCZOS)
# Border: 3 rows of dots in the handles' white, the logo's own dot size and spacing, out to
# the edge; neighbouring rows offset by half a dot so they interlock.
import math
S, SS = 384, 8
pitch = 0.70 * S / COLS
dot = pitch * 32 / 48
border = Image.new('RGBA', (S * SS, S * SS), (0, 0, 0, 0))
bd = ImageDraw.Draw(border)
for j in range(3):
    r = S / 2 - dot / 2 - j * pitch
    n = round(2 * math.pi * r / pitch)
    for i in range(n):
        a = 2 * math.pi * (i + 0.5 * (j % 2)) / n
        x, y = S / 2 + r * math.cos(a), S / 2 + r * math.sin(a)
        bd.ellipse([(x - dot / 2) * SS, (y - dot / 2) * SS, (x + dot / 2) * SS, (y + dot / 2) * SS],
                   fill=PALETTE['w'] + (255,))
readme.alpha_composite(border.resize((S, S), Image.LANCZOS))
readme.save(f'{REPO}/assets/icon_round.png')
# App icon, square: made from its own artwork, assets/app_icon_logo.png (owner), used as is,
# including its tiled background (extended with the same 12-px tile where a crop needs more).
# Sized so the whole logo fits inside a circle, for launchers that make icons round: legacy
# icon, logo ~72% of the square; adaptive foreground, logo ~44% of the canvas, so its box
# fits the 66/108 safe circle. The themed (monochrome) icon is the logo's shape alone.
art = Image.open(SRC.replace('logo.png', 'app_icon_logo.png')).convert('RGBA')
PAD = 240  # multiple of the 12-px tile, so the extended background lines up
icon_src = Image.new('RGBA', (art.width + 2 * PAD, art.height + 2 * PAD))
tile = art.crop((0, 0, 12, 12))
for ty in range(0, icon_src.height, 12):
    for tx in range(0, icon_src.width, 12):
        icon_src.paste(tile, (tx, ty))
icon_src.paste(art, (PAD, PAD))
ia = np.array(icon_src)
ICON_BRIGHT = 0.8  # owner: app icon 20% darker than the artwork
icon_src = Image.fromarray(np.dstack([(ia[:, :, :3] * ICON_BRIGHT).astype('uint8'), ia[:, :, 3:]]), 'RGBA')
lum = ia[:, :, :3].astype(int).sum(2)
ys, xs = np.nonzero(lum > 120)  # logo dots; the background tiles stay below this
cx, cy, lw = (xs.min() + xs.max() + 1) / 2, (ys.min() + ys.max() + 1) / 2, xs.max() + 1 - xs.min()

def icon_crop(fill_frac, side):
    half = lw / fill_frac / 2
    box = tuple(round(v) for v in (cx - half, cy - half, cx + half, cy + half))
    return icon_src.crop(box).resize((side, side), Image.LANCZOS)

mono = Image.fromarray(np.dstack([np.full(lum.shape, 255, 'uint8')] * 3 +
                                 [((lum > 120) * 255).astype('uint8')]), 'RGBA')
for dpi, s in {'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}.items():
    icon_crop(0.72, s).save(f'{REPO}/stage2/app/res/mipmap-{dpi}/ic_launcher.png')
    fg = s * 108 // 48
    icon_crop(0.44, fg).save(f'{REPO}/stage2/app/res/mipmap-{dpi}/ic_launcher_foreground.png')
    half = lw / 0.44 / 2
    mono.crop(tuple(round(v) for v in (cx - half, cy - half, cx + half, cy + half))).resize(
        (fg, fg), Image.LANCZOS).save(f'{REPO}/stage2/app/res/mipmap-{dpi}/ic_launcher_monochrome.png')
FAINT = 0.4  # brightness of the faint start-screen / loading-screen logo
# Start screen: faint logo at its own pixel size, pitch 6 / dot 4, pixel-exact dots
DOT4 = ['.##.', '####', '####', '.##.']
w, h = COLS * 6, ROWS * 6
faint = Image.new('RGBA', (w, h), (0, 0, 0, 0))
px = faint.load()
for r, row in enumerate(grid):
    for c, ch in enumerate(row):
        if ch == '.': continue
        col = tuple(int(v * FAINT) for v in PALETTE[ch]) + (255,)
        for yy, line in enumerate(DOT4):
            for xx, m in enumerate(line):
                if m == '#': px[c * 6 + 1 + xx, r * 6 + 1 + yy] = col
faint.save(f'{REPO}/stage2/app/res/drawable-nodpi/menu_logo.png')
open(f'{OUT}/grid.txt', 'w').write('\n'.join(grid) + '\n')
hi.save(f'{OUT}/master.png')
print('\n'.join(grid))
