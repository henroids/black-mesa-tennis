#!/usr/bin/env python3
"""tt-android: let the launcher turn off the game's own MSAA.

Rockstar Table Tennis renders 720p with multisampling. The Xbox 360's 10 MB of EDRAM
can't hold that in one piece, so the game's tiled-rendering setup (sub_82150500, the
equivalent of RAGE's grcDevice::BeginTiledRendering) splits the screen into strips and
the scene is drawn once per strip. In emulation every draw, resolve and EDRAM transfer
is expensive, so this multiplies the GPU work. With 1x sampling the frame fits in one
tile. Its fourth argument (r6) is the sample count (1, 2 or 4); this edit routes it
through TtGameMsaaOverride (main.cpp), which returns 1 when the setting is off.

Edits the locally generated code in place; idempotent.
"""
import re
import sys
from pathlib import Path

folder = Path(sys.argv[1])
FUNC = "sub_82150500"
MARK = "tt-android: game MSAA override"
start = re.compile(r"DEFINE_REX_FUNC\(" + FUNC + r"\) \{\n")
for path in sorted(folder.glob("*.cpp")):
    text = path.read_text(encoding="utf-8")
    m = start.search(text)
    if not m:
        continue
    if MARK in text:
        print(f"game MSAA override already in {path.name}")
        sys.exit(0)
    body_start = m.end()
    snippet = f"  ctx.r6.u64 = TtGameMsaaOverride(ctx.r6.u32);  // {MARK}\n"
    text = text[:body_start] + snippet + text[body_start:]
    first, rest = text.split("\n", 1)
    text = first + '\nextern "C" uint32_t TtGameMsaaOverride(uint32_t samples);\n' + rest
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"game MSAA override added to {FUNC} in {path.name}")
    sys.exit(0)
sys.exit(f"{FUNC} not found in generated code")
