# Hand-off: Table Tennis Android port (black-mesa-tennis)

This is a briefing for an AI assistant (or developer) picking up this project. It covers
the goal, how the build works, every reference project, what has been tried, what each
test showed, and where things stand now. Last updated 2026-11-05 (build **v82**, the wrap-up build).

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
| **This project** (public) | https://github.com/henroids/black-mesa-tennis | Build scripts, SDK patches, Android app. GPL-3.0. |
| PC static recomp (base) | https://github.com/davidptrssvea/Table-Tennis-Rockstar-PC | The recompiled-game project this port is built from (MIT). `stage1/overlay/project/*`, `native/main.cpp`, `tabletennis_app.h`, `CMakeLists.txt` adapted from it. |
| ReXGlue SDK v0.10.0 | https://github.com/rexglue/rexglue-sdk (release zip `rexglue-sdk-0.10.0-linux-amd64.zip`) | Static recompiler + runtime (Xenia-derived: Xenos GPU emulation on Vulkan, kernel HLE). Patched at build time by `stage2/native/patch_sdk_vulkan_fixes.py`. Upstream issues referenced: **#441, #456, #507**. |
| MCLA Android port (template) | https://github.com/gabrielbskk/mcla-recomp-android-g | Android build layout, SDL activity, custom GPU driver loader (`android_gpu_driver.cpp`). GPL-3.0, hence this repo's license. |
| libadrenotools | https://github.com/bylaws/libadrenotools | Loads custom Vulkan drivers (Turnip) on Adreno. |
| Turnip drivers tested | https://github.com/StevenMXZ/Adreno-Tools-Drivers/releases | Gen8 V38 / V37 builds (Mesa 26.3/27.0). |
| Xenia Canary (consulted) | — | Has only one Table Tennis patch (title 545407DF, disables light shafts as a crash fix); not needed here. |
| Naruto: Rise of a Ninja / The Broken Bond recomps (consulted) | https://github.com/jorjbarbosa/naruto-rise-recomp, https://github.com/jorjbarbosa/broken-bond-recomp | Same SDK. Took one setting from their SDK patches: `execute_unclipped_draw_vs_on_cpu=true` (v74 test). Their **native renderer** (hooks the game's D3D calls, draws with Vulkan directly, shaders pre-translated by XenosRecomp, no Xenos emulation) is the realistic route to 60 FPS, but it's ~50k lines, game-specific (hundreds of hooked addresses) and PC-only; porting it to Table Tennis (RAGE engine) and Android would be a multi-month project. Not started. |
| MCLA Android port, newer version (consulted 2026-11-05) | https://github.com/gabrielbskk/mcla-recomp-android-g (`config/render_perf.toml`, `src/render_perf.cpp`) | Has the same GPU-wait spin fix as our v76 test (`mcla_fence_spin`, from LARecomp: skips the 32 `mr r31,r31` no-ops in the fence poll and uses the CPU `yield` hint). Theirs is tuned for speed (core stays busy); ours sleeps 100 µs, tuned for lower load. Also single-tile rendering (we already do this via the MSAA override) and RAGE developer switches (no shadows / blur: changes the look, not used). |
| Table-Tennis-Rockstar-PC v1.0.6–v1.1.2 (2026-11-02) | CHANGELOG.md | Frame-pacing rework: one limiter at the start of the guest Swap with absolute deadlines, late-frame recovery. Smoothness, not load or FPS. Candidate to port later. |
| rac-rs (consulted) | https://github.com/AsobiCodes/rac-rs | PS2 Ratchet & Clank port in Rust; nothing applicable (its PGO targets CPU time; this port is GPU-bound). |
| **NFS Carbon recomp for Android** (consulted, v84) | https://github.com/Sampreeth004/nfs-carbon-recomp | Same SDK (v0.10.0), Adreno/Turnip. Went from 20–40 to ~60 FPS (GPU 34–49 ms → ~8 ms per frame) by replacing the SDK's Xenos emulation with **its own renderer plugin** (`renderer/`, ~9,000 lines, loaded through the SDK's `gpu_plugin` seam): reads the PM4 ring itself, translates Xenos microcode → GLSL → SPIR-V at run time (disk cache), real Vulkan images per render target instead of EDRAM emulation, resolves as image copies, persistent vertex arena, pass render area cropped to what is used. No game-side hooks. `CHANGELOG.md` and `docs/frame-census.md` are the write-up. **No licence file: ideas only, no code copied.** Its `patches/rexglue-android.patch` also carries the 'uploads before the pass' and 'cropped pass area' ideas for the Xenos backend (tests T1/T3 here are written independently). |
| **NFS Most Wanted for Switch / Android** (consulted, v84) | https://github.com/codepdbh/nfsmw-android | GPL-3.0 (same licence as this repo). Same SDK. 1–3 FPS with the emulated GPU → 30+ on a Switch 1 with a native renderer that reads the PM4 ring (27k lines, shaders pre-translated with XenosRecomp, needs ~21 game hooks). `docs/performance-history.md`, `docs/native-renderer.md`, `docs/porting-another-game.md` explain what paid off. |

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
- **Going back to an earlier build:** `bash tools/use_version.sh 61` checks out the commit
  for that version (found by its commit message `v61: ...`); `git checkout main && git pull`
  returns to the newest (older builds don't contain the script). Then build as usual. Builds from **v56** on rebuild cleanly (fresh SDK
  sources); ≤ v55 expected an SDK patched in place, so re-run stage 1 first for those.
  (Git tags can't be pushed from the assistant's environment, hence the script.)

### Branches
- **`main`** is the only branch. The earlier `copilot` branch was merged and removed.

### Repo layout
```
stage1/                 one-time setup script + overlay for the PC project
stage2/
  tt_android_stage2.sh  build script (copies native files, runs patches, packages APK)
  native/
    patch_sdk_vulkan_fixes.py  SDK changes: fixes F1–F11 + counters D1–D3 (header lists them)
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
tools/use_version.sh    switch to an earlier build by number
Install-Table-Tennis.cmd + tools/installer/install.ps1   Windows installer (WSL check, ISO picker,
                        root apt install, clone/pull, stage 1 + 2, opens Downloads)
assets/                 README images (original artwork only, never game assets)
docs/BUILDING.md        user-facing build guide + troubleshooting
.github/ISSUE_TEMPLATE/ problem-report template (asks for logs, forbids game files)
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
| v56 | **Cleanup**: fresh SDK sources each build; patch script rewritten as fixes F1–F11 + counters D1–D3; removed every finished experiment (kick, fence check, empty-submit, barrier merge/classes/skip, texture skip test, frames-in-flight, vblank/UI-rate overrides, GPU timestamps, timeline, mmio/audio/tiling/fence/tracker stats, TT_* switches) | Rallies **~41 FPS** (from ~39.4): the removed diagnostics/experiments had a real cost |
| v57 | Re-test round (Test row) | Same scene, FXAA on: Off ~40.8; **"No shadows" ~43.3 (+2.5)** but visible shadows remained: it skipped depth-only passes with pitches 1440, 640, 1120 (not the main shadows), no visible change noticed. Sysmem (AA off) ~41.2 ≈ no gain. Later runs (no-bin, 30 Hz, Off) were a much heavier scene (~1350 draws vs ~820, 39 vs 31 resolves) at ~30 FPS, so not comparable |
| v58/v59 | Test row: skip depth-only pass by pitch; Screen 30 Hz | Same scene, FXAA on, rally averages: Off 40.9; skip 1440 41.0 (that pass isn't used in rallies); **skip 640 41.8 (+0.9, ~37 draws/frame)**; **skip 1120 41.7 (+0.8, ~57 draws/frame)**; Screen 30 Hz 40.7 (no gain, removed). No visible change noticed in any |
| v59 | Game MSAA launcher row removed: always 1 sample (the v57 round's last four runs had it flipped On by accident, ~30 FPS); `TT_GAME_MSAA` switch removed | — |
| v60 | Test: skip depth passes 640 + 1120 together | Off 40.6 → **42.8 FPS (+2.2)**; screenshots matched (table and player shadows still drawn) |
| **v61** | **F10**: those two depth-only passes skipped permanently; Test row and depth-pitch counters removed | **Not tested yet** (expect ~42–43) |
| v62 | Render-pass census; Turnip sysmem; AA off | Rally averages: FXAA 42.8; **AA Off 44.1 (+1.3)**; sysmem+FXAA 43.2; sysmem+AA off 42.7 → sysmem is noise (dropped). AA stays a launcher choice (owner). Census per frame: ~404 draws 1280 colour+depth (main scene), **~96 at 1280 with no colour and no depth write (stencil-only: likely shadow volumes)**, ~73 half-size colour (640), ~46 full-size colour, ~33 quarter-size colour+depth (320), ~9 + ~4 smaller colour (bloom-like chain), plus F10's skipped 640/1120 depth-only |
| v63 | Skip one pass type at a time | Off ~42.9. **A** (1280, no colour, no depth write, ~96 draws): 43.4 (+0.5), no visible change. **B** (640 colour, ~73): 44.4 but background replaced by garbage and table shadow gone → B draws the background (likely the blurred backdrop) **and the real shadows**; keep. **C** (320, ~33): 43.7 but colours washed out → lighting/glow; keep |
| v64 | In game: **hold Back 0.6 s = screenshot** (PixelCopy of the SDL surface → Pictures/TableTennis/tt_<date>_<time>.png, toast confirms); tap Back still opens the return-to-start pop-up | — |
| v65 | Screenshot moved to **Select** (press and release on its own; Select+Start still opens the pop-up; the press still reaches the game). Back is tap-only again (owner's Back-hold closes apps system-wide) | — |
| v66 | Screenshot moved to **L3 + R3** (both stick buttons): Select changes the camera in game, and Android never delivers the Home key to apps. Select is untouched again | — |
| v67 | **F11**: pass A skipped permanently; test row and pass census removed. `tools/use_version.sh <n>` switches to any earlier build | **Not tested yet** (expect ~43.5) |
| v68 | Launcher **Hidden passes: Skipped (faster) / Drawn (original)** turns F10 + F11 off (`TT_DRAW_ALL_PASSES=1`) for quick comparison in other venues/characters/modes | — |
| **v69** | Test: pass B split into segments by draw order (new segment when the pixel shader changes within a frame); Test row skips segment 1–5 | Background never broke. Part 2 (~2 draws) gave oversized rectangular shadows (ball too). Parts 4 and 5 made shadows pop in and out: draw order changes between frames, so a 'segment' isn't the same thing every frame. ~44 FPS in rallies in all runs. Approach replaced in v70 |
| **v70** | Test: pass B split by pixel shader into 6 groups (hash % 6) | Bug: the hash had its low bit forced on, so only groups 2, 4, 6 had shaders (1/3/5 = same as Off). Pass B uses just 8 pixel shaders. G6 (5 shaders, ~50–65 draws, mostly `a452`) = background + shadows: skipping it broke the background and removed shadows. G2 (`f895` ~6 draws, `8cc4` 1) = the real shadows (none under the table or the ball). The 'choppy ball trail' note came from group 1, which skipped nothing, so it was most likely not the test. G4 (`635f`, 1 draw): nothing visible. Last-10 s rally FPS: Off 44.0, all of pass B skipped 44.7, so pass B is worth < 1 FPS |
| **v71** | Test: skip one pass-B shader at a time | Never run: owner chose to keep shadows (cheap, look good) and pinned the question |
| **v72** | Main scene pass round: census; gamma targets 8-bit; main pass clipped to left half; `TU_DEBUG=nolrz`; `TU_DEBUG=noubwc` | Last-10 s rally FPS: Off 44.2. Gamma 8-bit 44.3: no main-pass draw uses a gamma target, so it does nothing (dropped). **Left half 47.6**: halving main-pass pixels saves only ~1.6 ms, so pixel work there is ~3 ms of ~22.6 ms; the main pass isn't pixel-bound. No LRZ 44.9 (no effect: LRZ isn't helping). **No UBWC 39.0** (compression is worth ~5 FPS; already on). Main pass ≈ 390 draws/frame |
| **v73** | Where does the frame time go? Diagnostics (picture breaks): main pass with no pixels (vertex work only), main pass skipped, render-target copies (EDRAM transfers) skipped; counters for transfer draws and pixels | Not run; folded into v74 |
| **v74** | v73's diagnostics + `execute_unclipped_draw_vs_on_cpu=true` | Last-10 s FPS, Off 42.9. Main pass no pixels / skipped: **screen went black and the game stopped drawing the match** (15 / 304 draws per frame instead of ~775), so those numbers (55–56) aren't comparable; inconclusive. **RT copies skipped: 44.3 (+1.4)** and the rally looked normal (26 copy draws, ~4.7 screens of pixels per frame). Unclipped draws on CPU: 43.3 (noise), copies unchanged at 26/frame, CPU load unchanged (GPU thread 60% vs 59%); dropped |
| **v75** | Only test left: skip render-target copies, played longer (menus, replays, other venues/modes) to see if anything needs them. If nothing breaks, it joins the Hidden passes switch | Folded into v76 |
| **v76** | Two tests: skip render-target copies (played longer), and **calmer GPU wait**: the game's wait-for-GPU loop `sub_82359DC0` (D3D fence wait: checks the GPU's read pointer, calls `sub_823643E0`, then counts a stack variable down from 100 with the PPC low-priority hint, repeat) kept one big core ~80% busy all match. `stage2/native/patch_game_gpu_wait.py` inserts `TtGpuWaitPause(r11)` at `loc_82359E30`; with `TT_TEST=calmwait` it sleeps 100 µs on each countdown's first pass. Goal: less CPU load / heat at the same FPS Also: Shader threads row removed (owner), fixed at 4 (`--vulkan_pipeline_creation_threads=4`; only affects first-visit shader preparation) | Last-10 s FPS: Off 42.3, **Calmer GPU wait 43.0** with the spinning thread at **~21% of a core instead of ~86%** (top-6 threads ~146% vs ~228% CPU), no visible change. **Skip RT copies 43.7 (+1.4, same as v74)**, normal in the red-carpet hall and the Yasaka venue |
| **v77** | Both made permanent: GPU wait sleeps (always on, not a visual change); render-target copies skipped as **F12** under the Hidden passes switch. Test row, screenshot stamp and test counters removed | — |
| **v78** | Load-reduction round (no intended visual change), Test row: **Repaint on new frames** (`TT_TEST=newframes`: presenter waits before taking the guest image until the game has refreshed it, ≤~120 Hz, ≥10 Hz; overlay's own 60 Hz sleep skipped; counters `tt_present_stats`), **Less texture hashing** (`lesshash`: F9 doesn't hash GPU-written textures, and a CPU-written texture that changed on its last 8 loads is reloaded without hashing, re-checked every 16th load), **Both**, and **Stereo sound mix** (`TT_TEST=stereo`: Android reports a 6-channel output, so the game's 5.1 mix was folded to the Thor's speakers by Android; this uses the SDK's own stereo fold instead; owner hears ball hits as muffled/hollow/distant). Also: the "Loading main menu" screen (ImGui, `tabletennis_app.h`) now uses the start-screen look: GamePocket (copied from the APK to files/ and passed as `TT_UI_FONT`), 40/20 px = 2×/1× its pixel grid, white title, grey line, black Also: **frame pacing** ported from Table-Tennis-Rockstar-PC v1.0.6–v1.1.2 (limiter moved before `__imp__VdSwap`, absolute deadlines, late frames rebased; was a relative sleep after the swap), and a **40 FPS** frame-rate choice (pref `fps_v2`, old `fps` index migrated) that asks Android for the screen's 120 Hz mode (`preferredDisplayModeId`; 30/60 ask for 60 Hz; supported modes logged to logcat) | Last-10 s, all ~43.7 FPS. **Less hashing: 42 → 3.4 MB hashed per frame, GPU thread 64% → 47% of a core** (kept, permanent in F9). Repaint on new frames: no gain (SDLThread 25% → 21%). **Finding: the screen was repainted only ~22 times/s in every mode while the game made ~44 frames/s**: F8 makes the UI thread wait on the CPU for its paint, which sits in the GPU queue behind the game's queued frames, so about half the game's frames were never shown. Stereo / 40 FPS / loading screen not in this run (built before they were pushed) |
| **v79** | Tests: **Paint without waiting** (`TT_TEST=nowait`: the paint signals the present semaphore and the present waits GPU-side; acquire still on a CPU fence), **No wait + new frames only** (`nowaitnew`: plus the v78 repaint-on-new-frames wait), **Stereo sound mix**. 40 FPS option, frame pacing, loading screen also in this build | Stereo sound mix: "way better" (owner), maybe too much echo on the ball → permanent as **F13**. Last-10 s, 60 FPS cap: Off 43.6 game frames / **21.8 repaints/s**; Paint without waiting 43.5 / 60.2 (every frame shown, plus repeats); **No wait + new frames 43.7 / 43.6** (one paint per frame, GPU thread 31%). **40 FPS**: the Thor has 1080x1920 @60 and @120 (default 120); 40 FPS mode ran 39.7 game frames, GPU thread 25%, main thread 10% (much lower load). Owner: everything looked and played fine |
| **v80** | F13 stereo mix permanent; Test row: Paint without waiting, No wait + new frames only, **Less echo** (`TT_TEST=echo50`: rear channels 0.5 in the stereo fold instead of 0.707), **Much less echo** (`echo25`: 0.25) | Built v79 instead; echo tests carried to v81 |
| **v81** | Made permanent: **F8** UI thread no longer waits for its paint (present waits GPU-side), **F14** repaint once per new game frame (presenter; overlay's 60 Hz cap removed), D4 counters. Test row: Less echo / Much less echo only | Owner picked **Much less echo** (rear 0.25) |
| **v82** | Wrap-up: F13 rear channels fixed at 0.25; Test row and all test switches removed (no TT_TEST left). Final state: rallies ~43–45 FPS at 60, every frame shown; steady 40 at 120 Hz with much lower load | — |
| **v83** | Start screen: self-playing vertical Pong in the empty strip left of the menu (`PongView.java`, ported from the owner's canvas Pong in github.com/henroids/not_portfolio `js/script.js`; both paddles computer-controlled, pixel grid, start-screen colours; runs only while the start screen is in front) | — |
| **v84** | New optimisation round (goal: 60 FPS, less load). Source review found what earlier rounds missed: **the emulator ends and restarts the render pass for every upload** (`VulkanSharedMemory::UploadRanges` → `SubmitBarriers(true)`), ~310 times per frame, plus every texture load and resolve; render targets are 1280×2048 / 640×4096 and every pass covers all of it; a CPU write throws away up to 256 KB of GPU-buffer contents around it, so ~7 MB is re-uploaded per frame. Test row (`TT_TEST`): **Uploads before pass** (`hoist`, T1: an upload inside an open pass is recorded in front of the pass when no draw of that pass read those pages), **Memory watch 16 KB / 4 KB** (`watch16` / `watch4`, T2), **Crop pass area** (`area`, T3: pass area = union of its draws' scissors and clears), **No copies at all** (`nocopy`, T4: F12 without the empty pass and depth-store), **All four** (`all`), **All four + tiled GPU** (`allgmem`, also `TU_DEBUG=gmem`). New profile line: render passes per frame, uploads moved / could move / that cut the pass, memory-watch faults | Thor, Turnip V39, rallies: Off 46.5 (256 passes/frame); **Uploads before pass 52.3 (68 passes; 296 of 297 in-pass uploads moved)**; watch 16 KB 45.7 and 4 KB 45.4 (uploads 6.8 → 5.0 → 3.8 MB/frame and no texture hashing at 4 KB, but faults 37 → 172 → 340/frame, same upload count, no FPS); Crop area 45.3; No copies 45.3; All four 52.8; All + `TU_DEBUG=gmem` 47.8–52 (no better). |
| **v85** | **F15** uploads before the pass made permanent (off with Hidden passes → Drawn, like F10–F12). T2–T4 code, Test row and `TT_TEST` removed. Profile keeps the render-pass line. | — |
| **v86** | Measuring what the ~31 resolves per frame cost before building a direct copy. Test row: **Slow resolved textures** (`TT_TEST=freeze`: a GPU-written texture is converted only every 8th time) and **Slow resolves** (`noresolve`: the copy part of a resolve only every 8th time). Both break the picture on purpose. Every distinct resolve and resolved texture is written to `resolves.txt` in the logs. Load tracking: `cpu_threads.txt` now has whole-app CPU per sample and is archived per run in `runs/` (GPU busy % and clock were already in it). | Thor, V39, rallies: Off 52.7 FPS (GPU 99 %); Slow resolved textures 59.1 (GPU still ~99 %); **Slow resolves 59.8, capped, GPU ~80 % @ 615–680 MHz**. So the resolve chain is ~6 ms of the ~19 ms frame, the texture-conversion third alone ~2 ms. Shapes seen (`resolves.txt`): colour k_8_8_8_8 1x → texture format 6 (endian 8in32, swap R/B, tiled) at 1280×720, 640×360, 320×184, 160×96, 80×48, some via a 4x-MSAA view used as a 2×2 downsample; depth D24S8 → texture format 22 (k_24_8) at 1280×720, 1440×1280, 1120×704 (×2), 640×480 (×3). |
| **v87** | v86 tests and `resolves.txt` removed. Test row: **Skip unused resolves** (`TT_TEST=skipres`): depth resolves from the 640- and 1120-wide buffers whose draws F10 already skips (1120×704 ×2, 640×480 ×3 ≈ 10 of ~44 MB resolved per frame) are done only the first 4 times per destination. Profile counts them. | Thor, V39: Off 51.9 → **55.5 FPS**; 2.7 resolves skipped per frame, resolved bytes 43.5 → 36.2 MB/frame; GPU still 98 % @ 680 MHz, app CPU ~135 %; shadows unchanged in screenshots. |
| **v88** | **F16** made permanent (under Hidden passes); Test row removed. | — |
| **v89** | Test row: **Direct colour resolves** (`TT_TEST=direct`): a plain colour resolve (8888, 1x, no offsets, one render target owning the area, exactly one existing 8888 texture at the destination page) becomes one `vkCmdCopyImage` render target → host texture; guest memory is not written and the texture is not invalidated. The raw copy doesn't do the resolve's red/blue swap, so such a texture is sampled through a view with R and B swapped (`tt_rb_swapped_`, bindings reset when it flips). Render-target images gain TRANSFER_SRC usage. MSAA-sourced and depth resolves still take the old path. Counter in the render-pass profile line. | **Not tested yet** |

### Ruled out (no measurable effect)
Occlusion queries (the game makes none), frames in flight (beyond 3 hurts pacing), guest
vblank pacing, resolution (540p = 720p), AA/FXAA, Turnip sysmem/nobin, UI repaint rate,
sampler pool, EU vs US disc, the presenter's empty submits, "kicking" the GPU, the driver
version (all Turnip builds 36–38.5).

## Visual-risk changes (owner's rule: keep them easy to undo)

Changes that remove or skip rendering work, verified only in one venue (red-carpet hall),
a few opponents and exhibition rallies. Each must stay switchable or individually
removable, so a problem in another venue/character/mode can be checked and traced quickly.

| Change | Build | What it skips | How to turn it off |
|---|---|---|---|
| F10 | v61 | depth-only passes into 640- and 1120-wide depth buffers (~37 + ~57 draws) | launcher Hidden passes → Drawn |
| F11 | v67 | full-size pass writing neither colour nor depth (~96 draws) | launcher Hidden passes → Drawn |
| F12 | v77 | render-target copies between passes (~26 draws, ~5 screens of pixels per frame); seen fine in 2 venues | launcher Hidden passes → Drawn |
| F15 | v85 | uploads recorded in front of the open render pass when no draw of that pass read those pages (page-level check); no visual change expected or reported | launcher Hidden passes → Drawn |
| F16 | v88 | resolves of the 640- and 1120-wide depth buffers (the ones F10 no longer draws into) skipped after the first 4 per destination; shadows unchanged in one venue | launcher Hidden passes → Drawn |
| F9 (lower risk) | v55 | GPU re-conversion of textures whose guest bytes are unchanged | remove section F9 |
| Game MSAA 1 sample | ~v20 | the game's 4-sample tiled rendering | `TtGameMsaaOverride` in main.cpp |

When something looks wrong somewhere: switch Hidden passes to Drawn and compare. If it fixes
it, split the toggle per pass to find which one, then decide (keep drawn there / find a
narrower condition). For anything else, `bash tools/use_version.sh <n>` builds an older build.

## 6. Current understanding of the bottleneck

State at v82, AYN Thor (Adreno 740, Turnip Gen8 V38), rallies at 720p with FXAA:

- **The GPU is the limit**: kgsl reports it 99% busy at its top clock (680 MHz) in rallies.
  Vulkan timestamps undercount (~13 of ~23 ms), so don't trust them for totals.
- The work is spread thin. Halving the main pass's pixels only saved ~1.6 ms (v72); the
  F10–F12 skips, F9 and the game-MSAA override removed the big single items. What's left is
  Xenos emulation overhead spread over ~400 main-pass draws, ~30 resolves, ~60 texture loads,
  ~400 uploads and ~1000 barriers per frame, plus the game's own rendering.
- CPU: no longer a concern. The game's GPU-fence spin is now a sleep (~86% → ~21% of a core),
  texture hashing is ~3.5 MB/frame (was 42), the GPU thread is ~30–45% of a core.
- Presentation: since F8/F14 the screen shows every game frame (before v81 only about half).
- At 40 FPS on the 120 Hz screen the load drops a lot (GPU thread ~25%, game main thread ~10%).

### What the v84 review added

- **Render-pass restarts.** A phone GPU pays for every pass begin/end (state reload, flushes,
  waits). The Xenos backend restarts the pass for every shared-memory upload (~310 per frame
  for ~660 draws), every texture load and every resolve. v50 only skipped the barrier command,
  not the restart, so it didn't measure this. The NFS Most Wanted port measured the same thing
  on the same SDK: its 'uploads brought forward' switch, when turned off, cost ~15 ms of frame
  time.
- **Oversized passes.** Host render targets are as tall as the EDRAM allows (1280×2048,
  640×4096) and every pass's area is the whole target with load+store, 2.8–5.7× the pixels the
  game uses. Only matters when the driver renders a pass in tiles (GMEM).
- **Over-invalidation.** `SharedMemory::MemoryInvalidationCallback` widens every CPU write to
  256 KB. Everything in that block is uploaded again (and re-watched: mprotect on both the GPU
  thread and the game thread). The ~30 'CPU-written but unchanged' textures per frame (F9) are
  the same effect.
- **Resolves** (~31 per frame) each cost three full passes over the pixels: render target →
  EDRAM buffer, EDRAM → guest-format bytes in shared memory, then the texture load converts
  those bytes back into a host texture. v53 put the texture-load third alone at ~3 ms/frame.
  A direct render-target → texture copy is the next candidate after v84 (not built yet).
- **Why Switch 2 games run well on the same hardware**: those ports (e.g. BananaNative, 'NVN on
  Vulkan, no system emulation') run the game's ARM64 code directly and translate its graphics
  API calls to Vulkan one-to-one. Nothing imitates the console's GPU hardware. This port's cost
  is the Xenos/EDRAM imitation, not the game.

## 7. Suggested next steps

The owner considers the port finished at v82. If work resumes:

0. After F15 (~52 FPS, ~68 passes/frame, ~31 of them forced by resolves; v86: resolves ≈ 6 ms/frame, enough for a capped 60 with GPU headroom), in order: v87 skipres result; direct resolve → texture copy; ADPF performance hints (lets Android lower clocks when
   frames are easy; the NFS Carbon port uses it); the upstream fix `cf75b896` (vertex buffers
   bound at an unchanged address are never asked for again in v0.10.0).
0. **An own PM4-level renderer** (the NFS Carbon route) is the proven way to a locked 60 with
   headroom on this SDK: no game hooks, ~9–10k lines, replaces `rexgpu-xenos` through the
   `gpu_plugin` seam, Xenos backend kept as a fallback. Several weeks of test builds, since
   nothing can be run without the owner's device.
1. **A native (high-level) renderer** is the realistic way to a fixed 60 FPS: hook the game's
   D3D calls and draw with Vulkan directly, shaders pre-translated, no Xenos emulation. See the
   Naruto recomps in section 2. Multi-month: Table Tennis is RAGE-engine, and the reference is
   PC-only.
2. Smaller, still open: RAGE developer switches like the MCLA port's `perf_options` (visual
   changes, so owner's call); fewer barriers or texture invalidations inside the SDK (low
   expected gain per v49–v50).
3. Re-check the visual-risk table (F10–F12) whenever a new venue, character or mode looks wrong:
   Hidden passes → Drawn.

### Pinned / decided
- **Shadows / pass B**: keep the shadows (owner). Real shadows are shaders `f895` + `8cc4`
  (~7 draws); the background is mostly `a452`. All of pass B is only ~+0.7 FPS.
- **Turnip sysmem**: within noise in three measurements; not added.
- **AA Off** = +1.3 FPS; stays a launcher choice (owner).
- **40 FPS**: done (Frame rate option, asks for 120 Hz).
- **Frame pacing** from the PC version: done (v78).

## 8. Communication notes for the owner

- **Public-facing docs:** README.md is written for other people building the port for their own
  device: no game files, own legally obtained ISO only, never share the APK. Keep its
  settings guide and FPS numbers current when results change.

- **Small wins count (owner, 2026-11-01).** Big jumps are unlikely now; several +0.5–1 FPS
  changes add up. Re-testing earlier ideas is welcome, since results change as other
  bottlenecks are removed (the game is now GPU-bound). Run re-tests through one temporary
  launcher "Test" row and remove it once the round is done.
- **UI theme (owner, until further notice):** every UI (in-app start screen, pop-ups, the
  Windows installer) uses the same look: black background, GamePocket pixel font, white text,
  grey values, inverted white bar for the selected row, round grey button hints at the bottom right.
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
