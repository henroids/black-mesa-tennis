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
import math
BADGE_FILL = 0.70     # the paddles' width as a share of the round logo's diameter
BADGE_DROP = 10 / 384  # paddles moved down by this share of the diameter: looks balanced (owner)
BORDER = 0.9          # border dots: the handles' white, 10% darker (owner)


def round_logo(S, brightness=1.0, SS=8):
    """The project logo: the paddles on a black disc with a 3-row dotted border out to the edge
    (the logo's own dot size and spacing, neighbouring rows offset by half a dot so they
    interlock); transparent outside the disc."""
    big = Image.new('RGBA', (S * SS, S * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.ellipse([0, 0, S * SS - 1, S * SS - 1], fill=(0, 0, 0, 255))
    pitch = BADGE_FILL * S / COLS
    dot = pitch * 32 / 48
    off = (pitch - dot) / 2
    left = (S - COLS * pitch) / 2
    top = (S - ROWS * pitch) / 2 + BADGE_DROP * S
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch == '.': continue
            col = tuple(min(255, int(v * brightness)) for v in PALETTE[ch]) + (255,)
            x, y = (left + c * pitch + off) * SS, (top + r * pitch + off) * SS
            d.ellipse([x, y, x + dot * SS - 1, y + dot * SS - 1], fill=col)
    ring = tuple(int(v * BORDER * brightness) for v in PALETTE['w']) + (255,)
    for j in range(3):
        rad = S / 2 - dot / 2 - j * pitch
        n = round(2 * math.pi * rad / pitch)
        for i in range(n):
            a = 2 * math.pi * (i + 0.5 * (j % 2)) / n
            x, y = S / 2 + rad * math.cos(a), S / 2 + rad * math.sin(a)
            d.ellipse([(x - dot / 2) * SS, (y - dot / 2) * SS, (x + dot / 2) * SS, (y + dot / 2) * SS], fill=ring)
    return big.resize((S, S), Image.LANCZOS)


# README icon
round_logo(384).save(f'{REPO}/assets/icon_round.png')
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
# Start screen: the round logo, faint, drawn at its own pixel size behind the menu. Its paddles
# are on a 6-px dot pitch, the same as the loading screen's copy (tabletennis_app.h).
round_logo(round(6 * COLS / BADGE_FILL), FAINT).save(f'{REPO}/stage2/app/res/drawable-nodpi/menu_logo.png')
open(f'{OUT}/grid.txt', 'w').write('\n'.join(grid) + '\n')
hi.save(f'{OUT}/master.png')
print('\n'.join(grid))
