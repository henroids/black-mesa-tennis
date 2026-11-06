# Development log

This is the full record of how the port was built, one testing round at a time: what the
problem was, what we tried, what the logs showed, what we concluded from that, and what
changed as a result.

**How each round worked.** Claude wrote a build. Henry compiled it on his PC from his own
disc image, played it on his AYN Thor, and exported the app's log zip from the start screen.
Claude read the logs, worked out what they meant, and wrote the next build. Every number below
comes from those exported logs (FPS lines, `profile.txt`, `timeline.txt`, `cpu_threads.txt`)
or from Henry's notes on what he saw on screen. The raw logs themselves aren't in the
repository, because some of them (such as `game_hotcode.txt`) contain machine code from the
game. The figures were copied from them into commit messages and [HANDOFF.md](../HANDOFF.md)
as each round finished.

Unless a round says otherwise, the measurements are **gameplay rallies at 720p** on the AYN
Thor (Adreno 740) with the Turnip driver. "Last-10 s FPS" means the average over the last
10 seconds of a rally. Results within about ±0.5 FPS are treated as noise.

Short names used throughout:
- **CP**: the emulated Xbox 360 command processor, the thread that turns the game's
  graphics commands into Vulkan work.
- **KGSL**: the Qualcomm kernel graphics driver underneath Vulkan on Android.
- **F1–F14**: the permanent fixes in `stage2/native/patch_sdk_vulkan_fixes.py`, where each
  one is described at the top of the file.

---

## Phase 0: getting it running (before v29)

These builds were made before the GitHub repository existed, so only their outcomes were
recorded, not the full round-by-round data:

| Change | How we got there | Result |
|---|---|---|
| Android build of the ReXGlue SDK | Used the MCLA Android port's ARM64 patch, which applies cleanly to SDK v0.10.0 | Native Android library built (Oct 29, 2026) |
| **F1**: texture exponent bias read from fetch word 3 (upstream issue #456) | Fix for a known upstream bug | Skin and textures correct |
| **F2**: presenter keeps its swapchain format (#507) | Fix for a known upstream bug | Correct output (no paint-pipeline rebuild every frame) |
| **F3**: write-watch faults don't re-read `/proc/self/maps` (#441) | Fix for a known upstream bug | **~+30% speed** |
| **F4**: audio worker's multi-object wait sleeps instead of spinning | The audio wait was busy-spinning | Freed a CPU core |
| **Game MSAA off** (~v20–22) | The game asks for 4× MSAA, which makes the emulated GPU draw each frame in 2–3 tiled passes. Forcing 1 sample (`sub_82150500`, register r6) lets it draw in one | **~20 → ~29 FPS**, the largest single gain in the project |

---

## Phase 1: why is the GPU waiting? (v29–v36)

**v29 → v30: slow fence queries.**
- *Logs:* the CP's "has the GPU finished?" check cost **~13 ms per call** (26 ms per frame)
  inside KGSL, while the GPU itself finished each frame in ~9 ms.
- *Conclusion:* the driver call was the bottleneck, not the GPU.
- *Fix (F6):* each submission now ends by writing its number into memory the CPU can read,
  so checking progress no longer needs a driver call.
- *Result:* **~29 → ~36 FPS.**

**v30 → v31: the same problem in a second place.**
- *Logs:* 65% of the CP thread was inside `vkGetFenceStatus`, called from the presenter's
  submission tracker on every frame.
- *Fix (F7):* fences younger than 100 ms are treated as still pending, with no driver call.
- *Result:* the tracker's cost dropped to ~0.04 ms per frame. FPS didn't move (~33–36), so
  something else was also blocking.

**v31 → v32: presenting from the wrong thread.**
- *Logs:* the GPU command thread was blocked ~50% of the time in `RefreshGuestOutput`. It was
  presenting frames itself and waiting for the next screen refresh on every frame.
- *Fix:* present from the UI thread (`--host_present_from_non_ui_thread=false`).
- *Result:* still ~32 FPS; the thread was still ~45% blocked on a lock.

**v33: timing the lock.**
- *Logs:* `vkQueuePresentKHR` took **~15 ms** while holding the shared queue lock, and the
  GPU command thread waited **~22 ms per frame** for that lock. On Android, a present that
  waits on a semaphore can stall the whole queue.

**v34: present without semaphores (part of F8).**
- *Fix:* the paint signals no semaphore; the UI thread waits for it on the CPU, outside the lock.
- *Result:* the lock wait went to **0 ms** and the refresh to ~1.2 ms. But the game stayed at
  ~33 FPS, with the GPU command thread ~70% idle.

**v35: is the game paced by vblank?**
- *Hypothesis:* the game waits for a 60 Hz vblank interrupt, so a frame that just misses one
  waits a whole extra refresh.
- *Test:* emulated vblank at 120 and 240 Hz.
- *Result:* no change (~34 FPS). **Ruled out.**

**v36: sampler pool.**
- *Hypothesis:* draws were waiting for the sampler pool to free up.
- *Result:* **0 overflows** (budget 65,516). **Ruled out.** One ~16 ms wait per frame remained,
  against ~10 ms of the game's own GPU work.

Also in this phase: the shader cache is now kept across app updates, so each test build no
longer stutters while shaders recompile.

---

## Phase 2: better logs (v37–v40)

**v37: is the screen repaint in the way?**
- *Hypothesis:* the UI repaints the 1080p screen 60 times a second on the same GPU queue,
  even when the game hasn't made a new frame.
- *Test:* repaint at 60, 30 and 20 Hz.
- *Result:* **33.2 / 33.9 / 34.1 FPS**, so repaint rate doesn't matter. **Ruled out.**

**v38–v39: logging upgrades.** Each session now records its settings in plain words, play
time, a frame-time histogram, spikes of 80 ms or more, and sudden FPS drops. Up to this point,
it had sometimes been unclear which test option a log came from.

**v40: frame timeline.**
- *Added:* `timeline.txt`, a per-frame log of submits, GPU start and end, CP waits and swaps.
- *Finding:* the CP waited **~17 ms every frame** for work the GPU timestamps said had
  finished **50–70 ms earlier**. The progress marker only became visible at the next 60 Hz
  refresh.

---

## Phase 3: chasing the "late completion" gap (v41–v50)

**v41–v42: also check the fence while waiting.** v41 had a bug: a stats array overflow
switched the marker off, and FPS fell to the slow path (~27 ms per frame). v42 fixed that.
- *Result:* the fence query itself also blocks ~17 ms. **No gain (~36–37 FPS).**

**v43: "kick" the GPU.**
- *Hypothesis:* completion only shows up when the next submission arrives, so send a tiny one.
- *Result:* no change. **Ruled out.**

**Driver tests (no build needed).**
- *Turnip sysmem mode, sysmem without binning, AA off:* all ~36–37 FPS, so at this point the
  limit was not the GPU's pixel workload.
- *Driver comparison:* Mr. Purple T30 ~36.3, **Turnip Gen8 V38 ~38.5**, V37 ~38.3,
  V38 Patched ~38, Balemuni Apex v2 ~38. **V38 adopted.**

**v44: frames queued (3 / 4 / 6).**
- *Result:* 4 changed nothing. 6 gave ~38.5 FPS but **194 hitches of 80 ms or more**, because
  the game starts spinning on its own fences. **Reverted to 3.**

**v45:** each run's profile and timeline are now kept (newest 8), so driver comparisons have
full detail.

**v46: is GPU work running past its timestamps?**
- *Result:* the end-of-submission work takes ≈ **0.01 ms**, so no. New finding: about
  **1,000 pipeline barriers per frame**. Also, **540p gave the same FPS as 720p**.

**v47:** put every other user of the GPU queue on the timeline.
- *Result:* the presenter's paint and present aren't the gap, and there are no sparse binds.
  Once per frame, the GPU sat idle ~13 ms right after one of the presenter's empty
  fence-only submits.

**v48: give empty submits a real command buffer.**
- *Hypothesis:* KGSL handles empty submits slowly.
- *Result:* no change (~38). **Ruled out.**

**v49: merge back-to-back barriers.**
- *Result:* **0 merged**, because the barriers are never adjacent.

**v50: are barriers the cost?**
- *Test:* count barriers by kind, plus a test that skips the buffer-only ones.
- *Logs:* ~900 buffer-only, 0 memory-only and ~186 image barriers per frame. Skipping the
  buffer-only ones broke the picture, but gameplay stayed **~39 FPS** (menus 57 either way).
- *Conclusion:* **barrier cost ruled out.**
- *Cross-check:* Qualcomm's own driver (v863.1) ran ~25 FPS with graphical errors and showed
  the same per-frame gap (~21 ms), so the gap **isn't Turnip-specific**.

---

## Phase 4: the turning point, the game is GPU-bound (v51–v56)

**v51: log the real submit time.**
- *Logs:* batches reached the driver **~50 ms before** the GPU started them (the queue was 4
  deep), and the kernel driver reported the **GPU 99% busy at its top clock (680 MHz)** during
  rallies.
- *Conclusion:* the game **is GPU-bound**. The Vulkan timestamps undercount (they showed ~13 of
  ~26 ms per frame), and that's why Phases 1–3 looked like a waiting problem. Earlier readings
  of "4% busy at 615 MHz" had been stale.
- *New direction:* reduce the GPU's work instead of its waiting.

**v52–v53: how expensive are texture reloads?**
- *v52:* skipping all reloads gave a blank screen and invisible menus, so nothing could be
  measured.
- *v53:* reloading only 1 in 4 took rallies from **~38 → ~43 FPS** (choppy, with textures
  popping) and halved compute dispatches.
- *Conclusion:* re-converting textures is a real share of the GPU load.

**v54:** launcher cleanup. Finished test rows were removed, and the return pop-up was restyled.

**v55: skip textures that didn't change (F9).**
- *Fix:* when a reload is triggered only by CPU writes, hash the texture's bytes and skip the
  GPU conversion if they're unchanged. Textures the GPU itself wrote always reload.
- *Logs:* ~27 of ~59 loads per frame skipped; ~31 are GPU-written and must reload.
- *Result:* **~38.5 → ~39.4 FPS.**

**v56: cleanup.**
- *Change:* every finished experiment and diagnostic was removed, and the SDK is now patched
  from fresh sources on every build. The patch script went from ~2,000 to ~400 lines.
- *Result:* **~39.4 → ~41 FPS.** The leftover diagnostics had been costing real speed.
- *Standing rule from here on:* clean up as we go.

---

## Phase 5: removing GPU work without changing the look (v57–v67)

**v57: re-test the earlier ideas now that we know the game is GPU-bound.**
- *"No shadows" test:* **~43.3 vs ~40.8 FPS (+2.5)**, but the real shadows were still visible.
  It had skipped three depth-only passes (widths 1440, 640 and 1120), not the shadows.
- *Sysmem:* ~41.2, no gain.
- *Caveat:* the later runs in this round hit a much heavier scene (~1,350 draws vs ~820), and
  the last four had Game MSAA turned on by accident (~30 FPS), so they weren't comparable.

**v58–v59: test each depth pass on its own.**
- *Results:* off 40.9. Skip 1440: 41.0 (that pass isn't used in rallies). **Skip 640: 41.8
  (+0.9, ~37 draws per frame). Skip 1120: 41.7 (+0.8, ~57 draws).** Screen at 30 Hz: 40.7,
  dropped.
- No visible change in any of them.
- *Also (v59):* the Game MSAA option was removed and is now always 1 sample, since "On" had
  only ever been picked by accident.

**v60 → v61: skip both depth passes (F10).**
- *Result:* **40.6 → 42.8 FPS (+2.2)**, and screenshots matched (table and player shadows still
  drawn). v61 made it permanent.

**v62: render-pass census and re-tests.**
- *Re-tests:* FXAA 42.8; **AA off 44.1 (+1.3)**, which stays a launcher choice; sysmem+FXAA
  43.2 and sysmem+AA off 42.7, both noise, so sysmem was dropped.
- *Census per frame:* ~404 draws in the main scene; ~96 full-size draws that write neither
  colour nor depth (stencil only; "pass A"); ~73 half-size colour draws ("pass B"); ~46
  full-size colour; ~33 quarter-size ("pass C"); plus a small chain of bloom-like passes.

**v63: skip one pass type at a time.**
- Off: ~42.9.
- **Pass A:** 43.4 (+0.5), no visible change.
- **Pass B:** 44.4, but the background turned to garbage and the table shadow disappeared, so
  pass B draws the background *and* the real shadows. **Kept.**
- **Pass C:** 43.7, but colours washed out, so it's part of the lighting. **Kept.**

**v64–v66: a screenshot button** for reporting what's on screen.
- Hold Back: conflicted with Henry's system-wide Back-hold, which closes apps.
- Select: changes the camera in the game.
- **L3 + R3:** final.

**v67: skip pass A permanently (F11, ~+0.5 FPS).** Added `tools/use_version.sh` to go back to
any earlier build by number.

**v68: safety switch.** F10 and F11 had only been checked in one venue, so a launcher option,
**Hidden passes: Skipped / Drawn**, puts them back for a quick comparison anywhere.
HANDOFF.md lists every change that could affect the picture and how to undo it.

---

## Phase 6: the shadow hunt, and a decision (v69–v71)

**v69: split pass B into segments by draw order.**
- *Result:* the background never broke. Segment 2 (~2 draws) made oversized rectangular
  shadows, including on the ball. Segments 4 and 5 made shadows flicker in and out.
- *Conclusion:* draw order changes between frames, so a "segment" isn't the same thing from
  one frame to the next. The method was replaced.

**v70: split pass B by pixel shader instead.** (Test names are now stamped on screenshots.)
- *Bug found while reading the logs:* the grouping hash had its low bit forced on, so groups
  1, 3 and 5 were empty and behaved the same as Off.
- *Findings:* pass B uses only 8 pixel shaders.
  - Group 6 (5 shaders, ~50–65 draws, mostly shader `a452`) is the background plus shadows.
  - Group 2 (shaders `f895` ~6 draws, `8cc4` 1 draw) is **the real shadows**.
  - Group 4 (1 draw) had no visible effect.
- *A "choppy ball trail" report* came from group 1, which skipped nothing, so it most likely
  wasn't caused by the test.
- *Data:* off 44.0, all of pass B skipped 44.7, so **the whole of pass B is worth less than
  1 FPS.**

**v71: never run.** Henry's call: the shadows are cheap and look good, so **keep them**.

---

## Phase 7: where does the rest of the frame go? (v72–v77)

**v72: main scene pass tests.**
- Off: 44.2.
- Gamma targets at 8-bit: 44.3. No main-pass draw uses one, so it does nothing (dropped).
- **Main pass clipped to the left half: 47.6.** Halving its pixels saves only ~1.6 ms of
  ~22.6 ms, so **the main pass isn't limited by pixels.**
- `TU_DEBUG=nolrz`: 44.9 (no effect).
- **`TU_DEBUG=noubwc`: 39.0**, so the driver's compression is worth ~5 FPS and is already on.

**v73 → v74: main pass with no pixels / main pass skipped / render-target copies skipped,
plus "unclipped draws on CPU"** (an idea from the Naruto recompilations).
- Off: 42.9.
- The two main-pass tests made **the game stop drawing the match** (15 or 304 draws per frame
  instead of ~775), so their 55–56 FPS readings weren't comparable. Inconclusive.
- **Skipping render-target copies: 44.3 (+1.4)**, and the rally looked normal (26 copy draws,
  ~4.7 screens of pixels per frame).
- Unclipped draws on CPU: 43.3 (noise), with the same CPU load. Dropped.

**v75 → v76: two tests.**
- *Render-target copies, played longer:* **43.7 (+1.4, same as v74)**, normal in the
  red-carpet hall and the Yasaka venue.
- *Calmer GPU wait:* the game's own wait-for-GPU loop (`sub_82359DC0`, a D3D fence wait)
  kept one big CPU core ~80% busy all match. A patch (`patch_game_gpu_wait.py`) makes it sleep
  100 µs per check. Result: **43.0 vs 42.3, and that thread used ~21% of a core instead of
  ~86%** (top six threads ~146% vs ~228% CPU), with no visible change. MCLA's newer port turned
  out to have a similar fix; ours is tuned for lower load rather than speed.
- *Also:* the Shader threads row was removed (fixed at 4), at Henry's request.

**v77:** both made permanent. The copies skip became **F12** under the Hidden passes switch;
the GPU wait always sleeps.

---

## Phase 8: lower load, smoother picture, better sound (v78–v82)

**v78: load-reduction tests, no intended visual change.**
- **Less texture hashing:** **42 → 3.4 MB hashed per frame**, and the GPU thread went from
  **64% → 47%** of a core. Kept as part of F9.
- **Repaint only on new frames:** no gain (SDL thread 25% → 21%).
- **Key finding from the logs:** in every mode, the screen was repainted only **~22 times a
  second** while the game made **~44 frames a second**. The UI thread waited for its own paint,
  which sat in the GPU queue behind the game's frames, so **about half the frames the game drew
  were never shown.**
- *Also in this build:*
  - **Frame pacing** ported from Table-Tennis-Rockstar-PC v1.0.6–v1.1.2 (the limiter runs
    before the swap with absolute deadlines).
  - A **40 FPS option** that asks Android for the Thor's 120 Hz screen mode.
  - The "Loading main menu" screen restyled to match the start screen.
  - A **stereo sound mix** test. Henry heard ball hits as muffled, hollow and distant; Android
    was folding the game's 5.1 mix down by itself, and this uses the SDK's own stereo fold
    instead.

**v79: fix the missing frames.**
- Off: 43.6 game frames, **21.8 repaints/s**.
- Paint without waiting: 43.5 / 60.2 (every frame shown, plus repeats).
- **No wait + repaint on new frames only: 43.7 / 43.6, one paint per game frame**, GPU thread
  31%.
- **40 FPS mode:** the Thor offers 1080×1920 at 60 and 120 Hz. It ran 39.7 frames/s with the
  GPU thread at 25% and the main thread at 10%, a much lower load. Henry: everything looked and
  played fine.
- **Stereo mix:** "way better" (Henry), but maybe too much echo on the ball, so it became
  permanent as **F13**.

**v80–v81:** F8 (the UI thread no longer waits for its paint) and **F14** (repaint once per
new game frame) made permanent, so **every game frame now reaches the screen**. Echo tests:
rear channels at 0.5 ("less echo") or 0.25 ("much less echo"). Henry picked **much less echo**.

**v82: wrap-up.** Rear channels fixed at 0.25, and every remaining test switch removed.
- **Final state:** rallies at ~43–45 FPS with the 60 FPS setting and every frame shown, or a
  steady 40 FPS on the 120 Hz screen at much lower load.

**v83:** a self-playing Pong on the start screen, ported from Henry's own canvas Pong game.

After v83 came a new logo (white and red cross-stitch paddles). Henry tuned the brightness and
stitch thickness by eye over several builds: the menu logo at 20%, and the app icon reduced
from full brightness to 80% and then to about 70%.

---

## What didn't help (and why we're confident)

| Idea | Build | Measured |
|---|---|---|
| Faster emulated vblank (120/240 Hz) | v35 | No change, so the game isn't vblank-paced |
| Sampler pool | v36 | 0 overflows |
| Lower UI repaint rate (60/30/20 Hz) | v37 | 33.2 / 33.9 / 34.1 |
| Fence check / GPU kick | v41–v43 | No change; the query blocks just as long |
| More frames queued | v44 | 6: +1 FPS but 194 hitches, reverted |
| Empty-submit replacement | v48 | No change |
| Barrier merging / skipping | v49–v50 | 0 mergeable; skipping gave ~39 either way |
| Resolution 540p vs 720p | v46 | Same FPS |
| Turnip sysmem / no binning | no build, v57, v62 | Within noise three times |
| Screen at 30 Hz | v58 | 40.7 vs 40.9 |
| Gamma targets 8-bit, `nolrz` | v72 | No effect |
| Unclipped draws on CPU | v74 | Noise, same CPU load |
| Driver choice (all Turnip builds) | no build | 36–38.5; V38 best |
| EU vs US disc | no build | No difference |

## Where it stands

The GPU is the limit, and the remaining cost is spread thinly across emulating the Xbox 360's
GPU: ~400 main-pass draws, ~30 resolves, ~60 texture loads, ~400 uploads and ~1,000 barriers
per frame. A steady 60 FPS would most likely need a native renderer that hooks the game's
Direct3D calls directly, which would be a multi-month project. See HANDOFF.md sections 6–7.
