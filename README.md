# black-mesa-tennis

Android port of *Rockstar Games presents Table Tennis* (Xbox 360), built from the
community static recompilation and the ReXGlue runtime, tuned for the AYN Thor
(Snapdragon 8 Gen 2 / Adreno 740). Personal-use fan project — **no game files are
included**; you build from your own disc image.

## What's here

| Folder | What it is |
|---|---|
| `stage1/` | One-time setup: installs the Android SDK/NDK, fetches the ReXGlue SDK and the PC port, checks your ISO, recompiles the game code and builds the native libraries. 1–2 hours. |
| `stage2/` | Builds the installable APK: applies the SDK fixes, the game MSAA edit, the profiler, and packages the Android app (launcher, settings, driver manager, log export). |
| `tools/` | `pixel_crt.py`, a batch pixel-grid photo filter (unrelated helper). |

## Building (Windows 11 + WSL2 Ubuntu 24.04)

1. Install WSL: `wsl --install -d Ubuntu-24.04` in an admin PowerShell, reboot, pick a user name.
2. In Ubuntu, get this repo: `git clone https://github.com/henroids/black-mesa-tennis ~/black-mesa-tennis`
   (private repo: sign in when asked, or use a personal access token).
3. Stage 1 (once): `bash ~/black-mesa-tennis/stage1/tt_android_stage1.sh "/mnt/c/path/to/TableTennis.iso"`
4. Stage 2 (after every update): `cd ~/black-mesa-tennis && git pull && bash stage2/tt_android_stage2.sh`
   The APK is copied to your Windows Downloads folder as `TableTennis.apk`.

If a stage fails, it says which log zip to look at (`~/tt-android/tt-stage1-logs.zip` or
`tt-stage2-logs.zip`). They contain logs only, no game files.

## On the device

- **Start screen**: Play, game disc import, resolution, anti-aliasing, frame rate,
  GPU driver (import AdrenoTools/Turnip packages), Turnip mode, **Game MSAA**
  (Off = single-tile rendering, much faster), performance log, export logs, quit.
- **In game**: Select+Start (or Back) returns to the start screen.
- **Export logs** writes a zip with the runtime log, CPU/thread monitor, the sampling
  profile (`profile.txt`) and a device report.

## Performance notes so far

| Change | Effect |
|---|---|
| Write-watch fault path without `/proc/self/maps` parsing (#441) | ~+30% |
| Texture exponent bias from fetch word 3 (#456) | skin/textures render correctly |
| Audio worker multi-wait no longer busy-spins | frees a full CPU core |
| Game MSAA off → one EDRAM tile instead of 2–3 | 20 → ~29 FPS |
| Fence status queries cost ~13 ms each on KGSL drivers (v29 measurement) | v30: GPU progress from a memory marker → ~36 FPS |
| Presenter fence tracker hit the same slow query on every swap (v30 profile) | v31: young fences aren't queried (tracker cost ~0) |
| GPU command thread painted the screen itself and waited for the display refresh every frame (v31 profile) | v32: present from the UI thread |

## License

GPL-3.0 (see `LICENSE`); credits and third-party licenses in `NOTICE.md`.
