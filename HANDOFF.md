# Hand-off: Table Tennis Android port (black-mesa-tennis)

This is a briefing for an AI assistant (or developer) picking up this project. It covers
the goal, how the build works, every reference project, what has been tried, what each
test showed, and where things stand now. Last updated 2026-10-31 (build **v49**).

---

## 1. Goal and constraints

- **Goal:** a native Android ARM64 APK of *Rockstar Games presents Table Tennis* (Xbox 360)
  that runs at a **steady 60 FPS during gameplay** on an **AYN Thor**
  (Snapdragon 8 Gen 2, **Adreno 740**, Android 13, KGSL kernel driver).
- **Current result:** 60 FPS in menus; **~38 FPS during rallies** (Turnip Gen8 V38 driver).
- **Legal/data rules (important):**
  - The repo must never contain game code or data: no ISO, XEX, RPF, generated recompiled
    sources, APKs or `.so` files. `.gitignore` blocks these. The owner builds locally from his
    own disc image.
  - Don't ask the owner to upload the APK or generated code. He shares **logs only**
    (an in-app "Export logs" zip).
- **Owner preferences:** keep the in-game FPS overlay exactly as it is (an attempted restyle
  was rejected). The owner isn't a programmer: give him one command to run and a short,
  concrete test plan (which settings, how long to play, what to report).

## 2. Repositories and references

| What | Link | How it's used |
|---|---|---|
| **This project** (private) | https://github.com/henroids/black-mesa-tennis | Build scripts, SDK patches, Android app. GPL-3.0. |
| PC static recomp (base) | https://github.com/davidptrssvea/Table-Tennis-Rockstar-PC | The recompiled-game project this port is built from (MIT). `stage1/overlay/project/*`, `native/main.cpp`, `tabletennis_app.h`, `CMakeLists.txt` adapted from it. |
| ReXGlue SDK v0.10.0 | https://github.com/rexglue/rexglue-sdk (release zip `rexglue-sdk-0.10.0-linux-amd64.zip`) | Static recompiler + runtime (Xenia-derived: Xenos GPU emulation on Vulkan, kernel HLE). Patched at build time by `stage2/native/patch_sdk_vulkan_fixes.py`. Upstream issues referenced: **#441, #456, #507**. |
| MCLA Android port (template) | https://github.com/gabrielbskk/mcla-recomp-android-g | Android build layout, SDL activity, custom GPU driver loader (`android_gpu_driver.cpp`). GPL-3.0, hence this repo's license. |
| libadrenotools | https://github.com/bylaws/libadrenotools | Loads custom Vulkan drivers (Turnip) on Adreno. |
| Turnip drivers tested | https://github.com/StevenMXZ/Adreno-Tools-Drivers/releases | Gen8 V38 / V37 builds (Mesa 26.3/27.0). |
| Xenia Canary (consulted) | — | Has only one Table Tennis patch (title 545407DF, disables light shafts as a crash fix); not needed here. |

## 3. Build and delivery workflow

- The owner builds on **Windows 11 → WSL2 Ubuntu 24.04**, with Android NDK r30 and SDL3.
- **Stage 1** (once, 1–2 h): `stage1/tt_android_stage1.sh "<path to ISO>"` installs the
  SDK/NDK, fetches ReXGlue and the PC port, recompiles the game and builds native libraries.
  It writes into `~/tt-android/`.
- **Stage 2** (every update):
  `cd ~/black-mesa-tennis && git pull && bash stage2/tt_android_stage2.sh`
  This applies the SDK patches, the game MSAA patch and the profiler, builds the APK and
  copies `TableTennis.apk` to the Windows Downloads folder.
- **Gotcha:** stage 2 patches the **same SDK folder in place** on every build. Every patch in
  `patch_sdk_vulkan_fixes.py` must be **idempotent** (guarded by a marker string), and changes
  to an *already applied* patch don't take effect unless you add a separate repair step.
  This bit us in v41: a stats array was enlarged in an existing patch, the old size stayed,
  and the overflow disabled the GPU marker (fixed in v42 with a repair block).
- Commits are made by the assistant and pushed; the owner only pulls and builds.

### Branches (read this first)
- **`copilot`**: **your branch.** Commit and push all your work here. It started as an exact
  copy of `main` at build v49.
- **`main`**: the other assistant's (Claude's) line of work. Don't push to it. Good results
  can be merged into it later through a pull request.
- The owner switches between them like this:
  `git fetch && git checkout copilot && git pull && bash stage2/tt_android_stage2.sh`
  (`git checkout main` to go back).
- Both branches patch the **same SDK folder** on the owner's PC (`~/tt-android/...`, see
  stage 2). An SDK patch applied by one branch stays applied after switching. Keep patches
  idempotent and guarded by marker strings, and if a build misbehaves after a switch,
  re-extract the SDK from the release zip and rebuild.

### Repo layout
```
stage1/                 one-time setup script + overlay for the PC project
stage2/
  tt_android_stage2.sh  build script (copies native files, runs patches, packages APK)
  native/
    patch_sdk_vulkan_fixes.py  ~30 numbered, idempotent SDK patches (fixes + diagnostics)
    patch_game_msaa.py         patches generated sub_82150500 (RAGE tiled-rendering setup)
    main.cpp                   VdSwap hook: FPS counter, frame histogram, timeline swap event
    tabletennis_app.h          app setup, FPS overlay (leave as is), UI repaint cap
    tt_profiler.cpp            sampling profiler + stats report + timeline dump
    android_gpu_driver.cpp     custom driver loading (adrenotools)
  app/java/org/tabletennis/recomp/
    LauncherActivity.java      start screen (settings rows, driver import, export logs)
    Settings.java              settings → env vars / cvars; describe() for logs
    GameActivity.java          sets TT_* env vars, writes sessions.txt, archives runs
    LogExporter.java, ThreadMonitor.java, DriverManager.java, GameFiles.java
tools/pixel_crt.py      unrelated photo filter
```

## 4. Diagnostics built in (how to read the logs)

The owner exports a zip from the launcher. Key files:

- `game_sessions.txt`: one line per launch with **every setting**, including the driver.
- `game_tabletennis.log`: runtime log. `GAME FPS: x (avg, worst) [session m:ss]` every ~2 s,
  "FPS drop" and "frame spike" lines, `tt: env TT_*` lines.
- `game_profile.txt` (rewritten every 10 s; previous runs kept in `runs/` since v45):
  - stats lines: memory faults, audio, frame pacing, GPU work per frame (draws, resolves,
    texture loads/bytes, uploads), **GPU time per frame** (from Vulkan timestamps),
    submission tail, barrier count, GPU latency, fence/marker waits, kicks, merged barriers,
    and a whole-session frame-time histogram
  - per-thread SELF / GUEST / INCLUSIVE sample sections (guest functions as `sub_XXXXXXXX`)
- `game_timeline.txt` (and `runs/*_timeline.txt`): every 30 s, the last 1.5 s of events:
  CPU submit, GPU start/end per submission, command-processor waits, end-submission,
  guest SWAP, GPU kicks, presenter refresh/paint/present, sparse binds.
  **Caution:** GPU times are aligned to the CPU clock by the minimum observed
  (gpu_start − cpu_submit) offset, so absolute queue delays are only relative.
- `game_cpu_threads.txt`: per-thread CPU %, CPU clocks, temperatures.
- `syms/`: symbol lists for host offsets.

Useful env toggles (set by the launcher or for experiments): `TT_FENCE_MARKER=off`,
`TT_FENCE_CHECK=off`, `TT_GPU_KICK=off`, `TT_EMPTY_SUBMIT=keep`, `TT_MERGE_BARRIERS=off`,
`TT_FRAMES_IN_FLIGHT=1..6`, `TT_UI_HZ`, `TT_VBLANK_HZ`, `TT_GAME_MSAA=off|on`, `TT_PROFILE=<dir>`.

## 5. History: what was done and what it did

| Build(s) | Change | Result |
|---|---|---|
| early | Write-watch fault path skips `/proc/self/maps` re-parse (#441) | ~+30% |
| early | Texture exp_adjust read from fetch word 3 (#456) | Skin/textures correct |
| early | Presenter swapchain format fix (#507) | Correct output |
| early | Audio WaitMultiple polled in µs, no busy-spin | Freed a CPU core |
| ~v20–22 | **Game MSAA off**: `sub_82150500` r6 (sample count; game asks for 4) forced to 1 → single EDRAM tile instead of 2–3 predicated passes | **20 → ~29 FPS** |
| v29–30 | KGSL: `vkGetFenceStatus` / zero-timeout waits on unsignaled fences cost ~13 ms. Replaced with a **GPU progress marker** (vkCmdFillBuffer of the submission id into host-visible memory, CPU reads it) | **→ ~36 FPS** |
| v31 | Presenter's submission tracker skips status queries on young fences (<100 ms) | Tracker cost ~0 |
| v32 | `host_present_from_non_ui_thread=false` (present from the UI thread) | Removed vsync waits from the GPU thread |
| v34 | Present without semaphores (paint awaited on CPU; acquire with a CPU fence) | Queue-lock wait → 0 |
| v35 | Guest vblank at 240 Hz (`TT_VBLANK_HZ`) | No change → game not vblank-paced |
| v36 | Sampler-overflow counter | 0 overflows |
| v37–39 | UI repaint at 60/30/20 Hz; settings, session time and frame histogram in logs | No change (33.2 / 33.9 / 34.1) → screen repaint ruled out |
| v40 | **Frame timeline** | Command processor (CP) waits ~17 ms per frame for a submission whose GPU timestamps say it finished ~50–70 ms earlier |
| v41–42 | Also query the fence during the marker wait (v41 had the stats overflow bug, fixed in v42) | Fence query itself blocks ~17 ms; no gain (~36–37) |
| v43 | "GPU kick": tiny extra submission when a wait starts | No change |
| (no build) | Turnip sysmem, sysmem+nobin, AA off | All ~36–37 → not binning- or pixel-bound |
| v44 | Launcher "Frames queued" 3/4/6 | 4: no change; 6: ~38.5 but 194 hitches ≥80 ms (game then spins on its own fences, CP WAIT_REG_MEM) → reverted to 3 |
| (no build) | Drivers: Mr. Purple T30 ~36.3; **Turnip Gen8 V38 ~38.5**; V37 ~38.3; V38 Patched ~38; Balemuni Apex v2 ~38 | V38 adopted |
| v45 | Keep each run's profile/timeline (`logs/runs`, newest 8) | Tooling |
| v46 | Time the submission tail (marker barrier + fill) and count barriers | Tail ≈ 0.01 ms; **~1000 pipeline barriers per frame**; 540p same FPS as 720p |
| v47 | Presenter refresh/paint/present + sparse binds on the timeline | Gap isn't caused by paint/present; no sparse binds |
| v48 | Presenter's empty fence-only `vkQueueSubmit`s replaced by a 1-barrier command buffer | No change (~38) |
| **v49** | **Merge back-to-back pipeline barriers** in the deferred command buffer replay (never merges two that touch the same image/buffer; `TT_MERGE_BARRIERS=off`) | **Not tested yet** |

### Ruled out (no measurable effect)
Occlusion queries (the game makes none), frames in flight (beyond 3 hurts pacing), guest
vblank pacing, resolution (540p = 720p), AA/FXAA, Turnip sysmem/nobin, UI repaint rate,
sampler pool, EU vs US disc, the presenter's empty submits, "kicking" the GPU, the driver
version (all Turnip builds 36–38.5).

## 6. Current understanding of the bottleneck

Per gameplay frame (~26–28 ms, Turnip V38):

- The game thread spends ~95% of its time spinning in `sub_82359DC0` (BlockOnFence), waiting
  for the GPU emulation to catch up. The CP thread is ~30% busy. The CPU isn't the limit.
- There are two submissions per guest frame. Vulkan timestamps say ~13–14 ms of GPU work per
  frame (~7 ms per submission).
- Timeline pattern, every frame: after the frame's swap submission ends (by its last
  timestamp, which is now *after* the marker write), the GPU shows ~11–14 ms with **no
  timestamps** before the next queued submission starts. The marker for the finished
  submission becomes visible to the CPU at **the same moment** the next submission starts.
- Working hypothesis: the timestamps don't cover all of the GPU's real work. The Adreno 7xx
  may record them before the work actually drains (concurrent binning / BV vs BR pipes, or
  cache flushes at barriers), so the GPU is genuinely busy ~26 ms per frame. That work
  **doesn't scale with resolution**, which points at per-command overhead. The prime suspect
  is the ~1000 `vkCmdPipelineBarrier` calls per frame (each a full drain + cache flush on
  Adreno), plus ~340 render passes, ~570 copies, ~185 compute dispatches and ~43 MB of
  texture re-conversion per frame.
- Other per-frame costs worth attacking: texture re-uploads (~55–70 texture loads, ~43 MB per
  frame, caused by write-watch invalidations, ~1600–2000 faults/s) and render-target
  ownership transfers in Xenia's render target cache.

## 7. Suggested next steps

1. **Test v49** (barrier merge). Check `pipeline barrier calls saved by merging per frame`
   in the profile and the rally FPS. Watch for rendering errors (flicker, missing objects).
2. If merging saves few calls, find **where the barriers come from** (render target cache
   transitions, texture cache loads, shared-memory uploads, `SubmitBarriers`). Count per
   call site and reduce redundant ones (e.g. barriers with identical src/dst for unchanged
   layouts, per-texture barriers that could be batched before a pass).
3. Reduce **texture re-conversion**: why are the same textures reloaded every frame?
   Check whether the game or resolves write to pages overlapping texture memory,
   triggering write-watch invalidation. Caching or coarser invalidation could cut ~43 MB/frame.
4. Verify the **"hidden GPU time"** theory directly. For example, add TOP_OF_PIPE timestamps
   at submission start next to the BOTTOM_OF_PIPE ones, or time a submission with no
   barriers. Or check whether Turnip exposes a way to disable concurrent binning on a7xx.
5. Game-side detail reductions are an option if the emulator overhead can't be cut enough
   (the MCLA port did this: shadows/blur), along with the light-shaft pass.

## 8. Communication notes for the owner

- Give one command per build (`git pull && bash stage2/tt_android_stage2.sh`) and a short,
  concrete test: which launcher settings, ~2 minutes of rallies, export logs, report the FPS.
- Baseline settings for comparisons: **GPU driver Turnip Gen8 V38, Turnip mode Auto,
  720p, FXAA, Frame rate 60 FPS (console), Game MSAA Off, Frames queued 3, Shaders Wait,
  Fast memory watch On, Performance log On.** Remind him to reset experiment settings
  afterwards. He has left Sysmem or "20 Hz screen" switched on by accident before.
- Menu stutter on page changes is shader compilation. The cache persists across builds
  (`SHADER_CACHE_VERSION` in `LauncherActivity.java` clears it only when changed).
  "Shaders: Async" would hide it at the cost of pop-in.
