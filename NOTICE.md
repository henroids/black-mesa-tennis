# Credits and licenses

This repository contains build scripts, patches and an Android front end for running
the community static recompilation of *Rockstar Games presents Table Tennis* (Xbox 360)
on Android handhelds (developed on an AYN Thor). It contains **no game code or data**:
you need your own legally obtained copy of the game disc image, and the scripts
recompile it locally on your own machine.

- **Table-Tennis-Rockstar-PC** by davidptrssvea (MIT License,
  Copyright (c) 2026 davidptrssvea) — the PC port this is based on. Files adapted from
  it: `stage1/overlay/project/*`, `stage2/native/main.cpp`, `stage2/native/tabletennis_app.h`,
  `stage2/native/CMakeLists.txt`.
- **ReXGlue SDK** by Tom Clay (derived from the Xenia project) — the recompiler and
  runtime. `stage2/native/patch_sdk_vulkan_fixes.py` applies local fixes and diagnostics
  to it at build time; the SDK itself is downloaded by the stage 1 script.
- **MCLA Android port** (GPL-3.0) — the Android build layout, SDL activity setup and the
  custom GPU driver loader (`stage2/native/android_gpu_driver.cpp`) follow it. Because of
  that, this repository as a whole is distributed under the GPL-3.0 (see `LICENSE`).
- **SDL3** (zlib license), **libadrenotools** (BSD-2-Clause) — fetched at build time.
- **GamePocket** font by mumchristmas (SIL Open Font License 1.1) —
  `stage2/app/assets/fonts/`, license alongside it.
- Upstream issue fixes referenced in the patch script: rexglue-sdk #441, #456, #507.

Rockstar Games presents Table Tennis is a trademark of its respective owners. This is
an unofficial fan project for personal use and is not affiliated with or endorsed by
Rockstar Games, Take-Two Interactive or Microsoft.
