#!/usr/bin/env python3
"""tt-android: let the game's wait-for-GPU loop sleep instead of spinning.

sub_82359DC0 is the game's "block until the GPU has reached this point" wait (D3D's
fence wait). While the GPU is still busy it calls sub_823643E0 and then counts a stack
variable down from 100 in a tight loop (with the PowerPC "low priority" hint), over and
over. On the console that's cheap; here the profile shows it keeps one big CPU core
~80% busy for the whole match, because the game is always waiting for the GPU.

This edit adds a call at the top of that countdown loop (loc_82359E30). On the first
pass of each countdown (r11 == 100, right after "li r11,100") TtGpuWaitPause (main.cpp)
may sleep briefly; the GPU check itself is unchanged, so the game still sees the GPU
finish, just up to ~0.1 ms later. Whether it sleeps is decided at run time.

Edits the locally generated code in place; idempotent. If the generated code doesn't
look as expected it changes nothing and says so (the build carries on).
"""
import re
import sys
from pathlib import Path

folder = Path(sys.argv[1])
FUNC = "sub_82359DC0"
LABEL = "loc_82359E30:"
MARK = "tt-android: GPU wait pause"
start = re.compile(r"DEFINE_REX_FUNC\(" + FUNC + r"\) \{\n")
for path in sorted(folder.glob("*.cpp")):
    text = path.read_text(encoding="utf-8")
    m = start.search(text)
    if not m:
        continue
    if MARK in text:
        print(f"GPU wait pause already in {path.name}")
        sys.exit(0)
    end = text.find("\nDEFINE_REX_FUNC(", m.end())
    body = text[m.end(): end if end != -1 else len(text)]
    at = body.find(LABEL + "\n")
    if at == -1:
        print(f"GPU wait pause NOT added: {LABEL} not found in {FUNC} (code unchanged)")
        sys.exit(0)
    # r11 is emitted as a local ("r11") or as ctx.r11 depending on the codegen options.
    reg = "ctx.r11" if "ctx.r11." in body else "r11"
    if not re.search(r"(?<![\w.])" + re.escape(reg) + r"\.", body):
        print(f"GPU wait pause NOT added: r11 not found in {FUNC} (code unchanged)")
        sys.exit(0)
    insert_at = m.end() + at + len(LABEL) + 1
    snippet = f"\tTtGpuWaitPause({reg}.u32);  // {MARK}\n"
    text = text[:insert_at] + snippet + text[insert_at:]
    first, rest = text.split("\n", 1)
    text = first + '\nextern "C" void TtGpuWaitPause(uint32_t countdown);\n' + rest
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"GPU wait pause added to {FUNC} in {path.name} (register {reg})")
    sys.exit(0)
print(f"GPU wait pause NOT added: {FUNC} not found in generated code (code unchanged)")
