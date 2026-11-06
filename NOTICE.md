# Credits and licenses

This repository contains build scripts, patches and an Android front end for running
the community static recompilation of *Rockstar Games presents Table Tennis* (Xbox 360)
on Android handhelds (developed on an AYN Thor). It contains **no game code or data**:
you need your own legally obtained copy of the game disc image, and the scripts
recompile it locally on your own machine.

## Third-party work

| Component | License | How it's used here | License text |
|---|---|---|---|
| [Table-Tennis-Rockstar-PC](https://github.com/davidptrssvea/Table-Tennis-Rockstar-PC) by davidptrssvea | MIT, Copyright (c) 2026 davidptrssvea | The PC port this is based on. Adapted (and modified) files: `stage1/overlay/project/*`, `stage2/native/main.cpp`, `stage2/native/tabletennis_app.h`, `stage2/native/CMakeLists.txt` | [licenses/Table-Tennis-Rockstar-PC-MIT.txt](licenses/Table-Tennis-Rockstar-PC-MIT.txt) |
| [MCLA Android port](https://github.com/gabrielbskk/mcla-recomp-android-g) by gabrielbskk | GPL-3.0-only | Android build layout and SDL activity setup. `stage2/native/android_gpu_driver.cpp` is a **modified copy** of its `src/android_gpu_driver.cpp` (changed to load user-imported AdrenoTools driver packages). Its ReXGlue Android patch and patch script are **downloaded at build time**, not included here. | [LICENSE](LICENSE) |
| [ReXGlue SDK](https://github.com/rexglue/rexglue-sdk) by Tom Clay, portions derived from the [Xenia](https://xenia.jp) project | BSD-3-Clause | Recompiler and runtime, downloaded at build time. `stage2/native/patch_sdk_vulkan_fixes.py` applies local fixes and diagnostics to it (upstream issues #441, #456, #507 referenced). | [licenses/ReXGlue-BSD-3-Clause.txt](licenses/ReXGlue-BSD-3-Clause.txt) |
| [SDL3](https://libsdl.org) | zlib | Fetched at build time | [licenses/SDL3-zlib.txt](licenses/SDL3-zlib.txt) |
| [libadrenotools](https://github.com/bylaws/libadrenotools) | BSD-2-Clause | Fetched at build time | [licenses/libadrenotools-BSD-2-Clause.txt](licenses/libadrenotools-BSD-2-Clause.txt) |
| GamePocket font by mumchristmas | SIL Open Font License 1.1 | `stage2/app/assets/fonts/` | [GamePocket-OFL.txt](stage2/app/assets/fonts/GamePocket-OFL.txt) |

Because it includes GPL-3.0 code from the MCLA Android port, this repository as a whole
is distributed under the **GPL-3.0** (see [LICENSE](LICENSE)). The MIT, BSD and zlib
components keep their own notices above.

The app icon, start-screen logo and other images in `assets/` and `stage2/app/res/` are
original artwork for this project and contain no game assets.

## How this was made

The code in this repository was written with an AI assistant (Anthropic's Claude), directed
and tested on real hardware by the repository owner. The commit history shows every change.

## Trademarks

Rockstar Games presents Table Tennis is a trademark of its respective owners. This is
an unofficial, non-commercial fan project for personal use and is not affiliated with or
endorsed by Rockstar Games, Take-Two Interactive or Microsoft.
