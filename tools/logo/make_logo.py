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

def on_square(logo, side, fill_frac, bg):
    canvas = Image.new('RGBA', (side, side), bg)
    lw = round(side * fill_frac)
    lh = round(lw * logo.height / logo.width)
    lg = logo.resize((lw, lh), Image.LANCZOS)
    canvas.alpha_composite(lg, ((side - lw) // 2, (side - lh) // 2))
    return canvas

hi = render(48, 32)  # master, same proportions as the source (dot 8 / pitch 12)
# README icon: black square, logo filling ~80%
on_square(hi, 384, 0.80, (0, 0, 0, 255)).save(f'{REPO}/assets/icon.png')
# App icon, square: made from its own artwork, assets/app_icon_logo.png (owner: bigger dots,
# used as is), next to the logo source. Legacy icon: logo fills ~88% of the square; adaptive
# foreground: ~60% of the canvas, i.e. ~90% of the visible 72/108 square.
icon_src = Image.open(SRC.replace('logo.png', 'app_icon_logo.png')).convert('RGBA')
ia = np.array(icon_src)
ys, xs = np.nonzero(ia[:, :, :3].astype(int).sum(2) > 30)
icon_logo = icon_src.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
icon_logo.putalpha(Image.fromarray(((ia[ys.min():ys.max() + 1, xs.min():xs.max() + 1, :3]
                                     .astype(int).sum(2) > 0) * 255).astype('uint8')))
for dpi, s in {'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}.items():
    on_square(icon_logo, s, 0.88, (0, 0, 0, 255)).save(f'{REPO}/stage2/app/res/mipmap-{dpi}/ic_launcher.png')
    fg = s * 108 // 48
    on_square(icon_logo, fg, 0.60, (0, 0, 0, 0)).save(f'{REPO}/stage2/app/res/mipmap-{dpi}/ic_launcher_foreground.png')
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
