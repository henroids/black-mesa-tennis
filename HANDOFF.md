# Hand-off: Table Tennis Android port (black-mesa-tennis)

This is a briefing for an AI assistant (or developer) picking up this project. It covers
the goal, how the build works, every reference project, what has been tried, what each
test showed, and where things stand now. Last updated 2026-11-01 (build **v62**).

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
- **SDK sources are fresh every build (since v56).** Stage 2 keeps a clean copy of the SDK
  (`~/tt-android/rexglue-sdk-base`: tag v0.10.0 + Stage 1's Android patches, made once),
  copies its `src/` and `include/` to a staging folder, runs `patch_sdk_vulkan_fixes.py` on
  it, then `rsync -rc` copies only changed files into `~/tt-android/rexglue-sdk`. So patches
  apply exactly once to unmodified sources (no idempotency guards or repair steps), removing
  a patch really removes it, and unchanged files keep their timestamps (incremental rebuild).
- Commits are made by the assistant and pushed; the owner only pulls and builds.

### Branches (read this first)
- **`copilot`**: **your branch.** Commit and push all your work here. It started as an exact
  copy of `main` at build v49.
- **`main`**: the other assistant's (Claude's) line of work. Don't push to it. Good results
  can be merged into it later through a pull request.
- The owner switches between them like this:
  `git fetch && git checkout copilot && git pull && bash stage2/tt_android_stage2.sh`
  (`git checkout main` to go back).
- Switching branches is safe: each build re-applies that branch's SDK changes to fresh
  sources (see above).

### Repo layout
```
stage1/                 one-time setup script + overlay for the PC project
stage2/
  tt_android_stage2.sh  build script (copies native files, runs patches, packages APK)
  native/
    patch_sdk_vulkan_fixes.py  SDK changes: fixes F1–F10 + counters D1–D3 (header lists them)
    patch_game_msaa.py         patches generated sub_82150500 (RAGE tiled-rendering setup)
    main.cpp                   VdSwap hook: FPS counter, frame-time histogram, MSAA override
    tabletennis_app.h          app setup, FPS overlay (leave as is), UI repaint cap
    tt_profiler.cpp            sampling profiler + per-frame stats report (profile.txt)
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
- `game_profile.txt` (rewritten every 10 s; previous runs kept in `runs/`):
  - session settings; frame pacing (guest vblanks/s, game frames/s, CP wait-for-memory);
    GPU work per frame (draws, resolves, texture loads/KB, uploads/KB); texture loads per
    frame (asked / skipped because unchanged / GPU-written / MB hashed); whole-session
    frame-time histogram
  - busy threads, and per-thread SELF / GUEST / INCLUSIVE samples (`sub_XXXXXXXX`)
- `game_hotcode.txt`: guest machine code of the hottest functions (once, at 60 s).
- `game_cpu_threads.txt`: per-thread CPU %, CPU clocks, temperatures, **GPU busy % and clock**
  (kgsl; with Turnip V38 it reads 99% @ 680 MHz in rallies).
- `syms/`: symbol lists for host offsets.

Env switches the native side still reads: `TT_PROFILE=<dir>`
(launcher, Performance log), `TT_SETTINGS` (launcher, for the logs). All experiment switches
were removed in v56; the history table below says what each experiment found.

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
| v49 | Merge back-to-back pipeline barriers in the deferred command buffer replay (`TT_MERGE_BARRIERS=off`) | **0 merged**: barriers are never adjacent; ~38 FPS |
| v50 | Classify barriers (buffer-only / memory-only / image) + launcher **Sync test** = skip buffer/memory-only barriers (`TT_SKIP_BUFFER_BARRIERS=1`) | Gameplay frame: ~900 buffer-only, 0 memory-only, ~186 image barriers. Skipping buffer barriers glitched the picture but gameplay stayed **~39 FPS** (menus 57 either way) → **barrier cost ruled out** |
| (no build) | Qualcomm proprietary driver v863.1 | ~25 FPS + graphical errors; the per-frame GPU gap is there too (~21 ms) → not Turnip-specific |
| v51 | Timeline records the real `vkQueueSubmit` (event 15) | Batches reach the driver ~50 ms before the GPU starts them (queue 4 deep), and **kgsl reports the GPU 99% busy at its top clock (680 MHz)** in rallies → the game **is GPU-bound**; Vulkan timestamps undercount (~13 of ~26 ms/frame). Earlier "4% @ 615 MHz" readings were stale |
| v52 | "Skip texture reloads (test)": each texture loaded only once | Screen blank, menus invisible → unplayable, no measurement |
| v53 | "Fewer texture reloads (test)": first load normal, then only every 4th reload | Rallies **~38 → ~43 FPS** (choppy, textures popping); compute dispatches halved → texture re-conversion is a real share of the GPU load |
| v54 | Launcher cleanup: removed Turnip mode, Fast memory watch (always on), Frames queued, Sync test (buffer skip), 540p, 30/20 Hz screen tests, retired experiments/pacing/occlusion. The only test row is **Performance test** (Off / Fewer texture reloads). Return-to-start pop-up restyled like the start screen | — |
| v55 | **Texture hash skip**: on a reload caused only by CPU writes, hash the guest bytes (XXH3) and skip the GPU re-conversion if unchanged since the last load; GPU-written (resolved) textures always reload (`TT_TEX_HASH=off`). Profile line `texture loads per frame: asked / skipped / GPU-written / MB hashed`. Launcher: Performance test row removed; Play's green focus box removed | Rallies ~38.5 → **~39.4 FPS**; ~27 of ~59 loads/frame skipped, ~31 are GPU-written (resolves) and must reload |
| v56 | **Cleanup**: fresh SDK sources each build; patch script rewritten as fixes F1–F10 + counters D1–D3; removed every finished experiment (kick, fence check, empty-submit, barrier merge/classes/skip, texture skip test, frames-in-flight, vblank/UI-rate overrides, GPU timestamps, timeline, mmio/audio/tiling/fence/tracker stats, TT_* switches) | Rallies **~41 FPS** (from ~39.4): the removed diagnostics/experiments had a real cost |
| v57 | Re-test round (Test row) | Same scene, FXAA on: Off ~40.8; **"No shadows" ~43.3 (+2.5)** but visible shadows remained: it skipped depth-only passes with pitches 1440, 640, 1120 (not the main shadows), no visible change noticed. Sysmem (AA off) ~41.2 ≈ no gain. Later runs (no-bin, 30 Hz, Off) were a much heavier scene (~1350 draws vs ~820, 39 vs 31 resolves) at ~30 FPS, so not comparable |
| v58/v59 | Test row: skip depth-only pass by pitch; Screen 30 Hz | Same scene, FXAA on, rally averages: Off 40.9; skip 1440 41.0 (that pass isn't used in rallies); **skip 640 41.8 (+0.9, ~37 draws/frame)**; **skip 1120 41.7 (+0.8, ~57 draws/frame)**; Screen 30 Hz 40.7 (no gain, removed). No visible change noticed in any |
| v59 | Game MSAA launcher row removed: always 1 sample (the v57 round's last four runs had it flipped On by accident, ~30 FPS); `TT_GAME_MSAA` switch removed | — |
| v60 | Test: skip depth passes 640 + 1120 together | Off 40.6 → **42.8 FPS (+2.2)**; screenshots matched (table and player shadows still drawn) |
| **v61** | **F10**: those two depth-only passes skipped permanently; Test row and depth-pitch counters removed | **Not tested yet** (expect ~42–43) |
| **v62** | Test round: render-pass census in the profile (draws per frame by pitch / colour format / colour+depth writes) to find the real shadows; Test row "Turnip sysmem"; AA Off via the normal row | **Not tested yet** |

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

> **Update (v51):** the bottleneck is GPU load. The GPU is 99% busy at 680 MHz during rallies,
> and the per-frame "gap" in the timeline is GPU work the timestamps don't capture. Note that the
> launcher's 540p option may not lower the game's internal render resolution, so pixel cost
> isn't fully ruled out. Next: measure categories by removing them (texture reloads first, v52),
> then resolves/RT transfers, then game-side effects (depth-of-field blur in replays, shadows,
> light shafts).

## 7. Suggested next steps

### Pinned (revisit for small wins)
- **Turnip sysmem** (`TU_DEBUG=sysmem`): v57 ~41.2 vs ~40.8 baseline, but it ran with AA off,
  so sysmem and AA-off weren't isolated. Re-test each alone in the same scene.
- **Anti-aliasing Off** alone: never measured on its own since the game became GPU-bound.


1. The ~11–14 ms GPU gap after each frame's swap submission is **also present in menus** (where
   frames are short enough to absorb it), so it's a fixed per-frame latency, not workload.
   It matches the ~12–14 ms the KGSL fence query blocks for. Suspect: the driver/kernel only
   releases the next submission once the previous one's completion is processed. Next A/B:
   Qualcomm's proprietary driver (e.g. v863.1 for A7xx) vs Turnip V38.
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

- **Small wins count (owner, 2026-11-01).** Big jumps are unlikely now; several +0.5–1 FPS
  changes add up. Re-testing earlier ideas is welcome, since results change as other
  bottlenecks are removed (the game is now GPU-bound). Run re-tests through one temporary
  launcher "Test" row and remove it once the round is done.
- **Clean up as you go (owner's standing rule).** When an experiment is finished, remove its code,
  env switches, launcher rows and counters in the same build that reports the result. Keep only
  fixes that measurably help and the diagnostics we actually read. Don't let dead code pile up.

- Give one command per build (`git pull && bash stage2/tt_android_stage2.sh`) and a short,
  concrete test: which launcher settings, ~2 minutes of rallies, export logs, report the FPS.
- Baseline settings for comparisons (v54 launcher): **GPU driver Turnip Gen8 V38, 720p, FXAA,
  60 FPS, Shaders Wait, Performance log On.** (Game MSAA is always off since v59.) Retired
  options are still env toggles natively (TU_DEBUG, TT_FRAMES_IN_FLIGHT, TT_UI_HZ, ...) but
  no longer in the launcher. Keep the launcher lean: remove test rows once a test is done.
- Menu stutter on page changes is shader compilation. The cache persists across builds
  (`SHADER_CACHE_VERSION` in `LauncherActivity.java` clears it only when changed).
  "Shaders: Async" would hide it at the cost of pop-in.
