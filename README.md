<p align="center">
  <img src="assets/icon.png" width="128" alt="black-mesa-tennis icon (pixel table-tennis paddle)">
</p>

<h1 align="center">black-mesa-tennis</h1>

<p align="center">
  An unofficial native <b>Android (ARM64)</b> port of <i>Rockstar Games presents Table Tennis</i> (Xbox 360),<br>
  built from the community static recompilation and tuned for Snapdragon handhelds such as the <b>AYN Thor</b>.
</p>

> [!IMPORTANT]
> **This repository contains no game code, data or assets, and never will.**
> You need **your own legally obtained retail copy** of the game, as a disc image (ISO) made from
> your own disc. The build scripts read that ISO on your own computer and turn it into an APK for
> **your own device**. Please don't ask for, link to or share ISOs, and **don't share the APK you
> build**: it contains data from your copy of the game. This project doesn't support piracy in
> any form.

> [!NOTE]
> **Upfront: I didn't write this. Claude, Anthropic's AI, did.** Nearly all of the code here was
> written by AI. See [How this was made](#how-this-was-made) for exactly who did what.

---

## Contents
- [How this was made](#how-this-was-made)
- [Status](#status)
- [What you need](#what-you-need)
- [Building the APK](#building-the-apk)
- [Installing and first launch](#installing-and-first-launch)
- [Recommended settings](#recommended-settings)
- [Settings guide (what each option does)](#settings-guide)
- [Controls](#controls)
- [Known issues](#known-issues)
- [Reporting problems](#reporting-problems)
- [Repository layout](#repository-layout)
- [Credits and license](#credits-and-license)

## How this was made

This project was built with AI, and I want to be completely clear about that.

**I'm not a programmer.** Nearly all of the code in this repository was written by
[Claude](https://www.anthropic.com/claude), an AI model made by Anthropic. Over about a week
(late October to early November 2026) Claude went from an empty repository to build v83, across
more than 90 commits. Every one of those commits is authored by Claude, so the history shows
exactly what it wrote and when.

### What Claude did
- **Wrote the code:** the stage 1 and stage 2 build scripts, the Windows installer, the patches
  that make the ReXGlue SDK run on Android's Vulkan drivers, the game-specific patches (such as
  turning off the game's MSAA and calming its GPU-wait loop), and the whole Android app, including
  the launcher, settings, driver import, log export and thread monitor.
- **Did the performance work:** added the profiler and frame-time logging, read the logs I sent
  back, worked out where the bottlenecks were, and took the port from about 20 FPS in rallies to
  about 44.
- **Did the research:** read the upstream projects (the PC recompilation, ReXGlue, and the MCLA
  Android port) to work out how to combine them, and tracked down known upstream issues.
- **Wrote the docs:** this README, [docs/BUILDING.md](docs/BUILDING.md),
  [HANDOFF.md](HANDOFF.md), and the licensing notices.

### What I did
- **Had the idea and set the direction:** porting the PC recompilation to Android so it runs
  natively on my AYN Thor, plus what the app should do and what was worth chasing.
- **Ran every build and tested every one on real hardware.** Claude can't run the Android
  toolchain or touch a device, so every build was compiled on my PC from my own disc image and
  played on my Thor. Nothing here was verified any other way.
- **Reported back:** after each build I sent the logs, screenshots and FPS numbers, and described
  what looked or felt wrong.
- **Made the calls:** the look of the app and its logo, which settings to keep or drop, and the
  trade-offs. For example, I chose to keep the original visuals and shadows instead of trading
  them for frame rate.
- **Decide what ships.** I own the repository and choose what goes in it.

### What that means for you
- The code wasn't written or reviewed by an experienced developer, so expect rough edges and
  read it with that in mind. Issues and fixes are welcome; I'll keep feeding them back to Claude.
- The real heavy lifting was done by the people credited [below](#credits-and-license). This
  port stands on their recompilation, SDK and Android work.

## Status

Playable. Measured on an **AYN Thor** (Snapdragon 8 Gen 2 / Adreno 740)
with the recommended settings below:

| Part of the game | Frame rate |
|---|---|
| Menus, character select | ~57–60 FPS |
| Rallies (720p, FXAA, 60 FPS setting) | ~43–45 FPS |
| Rallies (40 FPS setting, 120 Hz screen) | steady 40 FPS, much lower CPU/GPU load |
| Rallies (720p, anti-aliasing Off) | ~45–46 FPS |

A steady 60 FPS in matches isn't reached: during rallies the graphics chip is the limit (it runs
at 99% at its top clock). The realistic route to 60 would be a native renderer instead of Xbox 360
GPU emulation, a much bigger project; see [HANDOFF.md](HANDOFF.md) for that and for the full
history of what was tried and measured.

Other Android devices with a recent Adreno GPU may work but haven't been tested.

## What you need

**Game**
- Your own retail **Xbox 360** disc image (`.iso`) of *Rockstar Games presents Table Tennis*,
  **USA or Europe** release (Title ID `545407DF`). The ISO is checked before anything is built,
  and other versions are rejected. It is only ever read, never modified.

**A computer to build on**
- **Windows 10/11 with WSL2** (Ubuntu 24.04), or Ubuntu 24.04 directly.
- An internet connection for the first build (it downloads the Android SDK/NDK and the
  open-source projects listed under [Credits](#credits-and-license)).
- Plenty of free disk space (20 GB or more is comfortable) and 1–2 hours for the first build.

**A device to play on**
- Android 10 or newer, ARM64, with Vulkan.
- Developed and tuned on the AYN Thor (Snapdragon 8 Gen 2). A controller is required (built-in
  controls on handhelds work).
- Recommended: a **Turnip** GPU driver package (see [Recommended settings](#recommended-settings)).

## Building the APK

### Easy way: the Windows installer

1. On this page, **Code → Download ZIP**, and unzip it anywhere.
2. Double-click **`Install-Table-Tennis.cmd`**. Use the arrow keys or mouse to move, Enter to
   select, and Esc to quit.
3. If WSL/Ubuntu isn't installed yet, select **Install Ubuntu (WSL)** (asks for administrator
   permission). Restart if Windows asks, finish Ubuntu's first-time setup (user name and
   password), and double-click the installer again.
4. **Game disc** → pick **your own ISO**, then **Build APK**. The installer installs the build tools, gets
   the latest version, checks your ISO, builds everything (1–2 hours the first time), and
   puts `TableTennis.apk` in your **Downloads** folder (**Open APK folder** shows it).

Run it again whenever you want the latest version. It remembers your ISO and skips finished
steps, so updates take minutes.

### Manual way (any OS with Ubuntu 24.04)

Details and troubleshooting are in [docs/BUILDING.md](docs/BUILDING.md):

1. **Windows only:** install WSL with Ubuntu. In an *administrator* PowerShell:
   `wsl --install -d Ubuntu-24.04`, reboot, and pick a user name and password.
2. In the **Ubuntu** window, get this repository:
   ```bash
   git clone https://github.com/henroids/black-mesa-tennis ~/black-mesa-tennis
   ```
3. **Stage 1 (once):** point it at your ISO. A Windows path like `C:\Games\TableTennis.iso`
   becomes `/mnt/c/Games/TableTennis.iso`. Keep the quotes:
   ```bash
   bash ~/black-mesa-tennis/stage1/tt_android_stage1.sh "/mnt/c/Games/TableTennis.iso"
   ```
   This sets up the toolchain, checks your ISO and recompiles the game code (1–2 hours).
4. **Stage 2 (every time you update):**
   ```bash
   cd ~/black-mesa-tennis && git pull && bash stage2/tt_android_stage2.sh
   ```
   The finished `TableTennis.apk` is copied to your Windows **Downloads** folder.

If a stage fails, it says so and leaves a log zip (`~/tt-android/tt-stage1-logs.zip` or
`tt-stage2-logs.zip`). Those contain build logs only, no game files.

## Installing and first launch

1. Copy `TableTennis.apk` to your device and install it (allow installs from your file manager
   if Android asks).
2. Open **Table Tennis**.
3. **Game disc → Choose**, and pick your ISO on the device. It is copied into the app's own folder
   once (keep the screen on until it says *Disc ready*).
4. *(Recommended)* **Import driver**, pick a Turnip driver package (`.zip` / `.adpkg`), and select it
   under **GPU driver**.
5. Choose **Play**. The first time you visit each screen, there's a short stutter while shaders
   are prepared. They're kept for later sessions, so it settles down after one pass through the
   menus.

## Recommended settings

| Setting | Recommended | Notes |
|---|---|---|
| Resolution | **720p** | The game's native resolution |
| Anti-aliasing | **FXAA** (or **Off** for ~+1.3 FPS) | Off shows more jagged edges |
| Frame rate | **60 FPS** | The game's own rate |
| Shaders | **Wait (complete)** | No missing objects; brief stutter on new screens only |
| GPU driver | **Turnip Gen8 V38** | From [StevenMXZ/Adreno-Tools-Drivers](https://github.com/StevenMXZ/Adreno-Tools-Drivers/releases) (`Turnip_Gen8_V38.zip`) |
| Hidden passes | **Skipped (faster)** | Switch to *Drawn* if something looks wrong (see below) |
| Performance log | Off for normal play, **On** when reporting a problem | |

## Settings guide

What to expect from each option, measured on the AYN Thor during rallies (FPS differences are
approximate, compared with the recommended settings):

| Setting | Options | Effect on playability and frame rate |
|---|---|---|
| **Resolution** | 720p · 1440p (2×) | 1440p renders at twice the width and height: sharper, but much heavier, so expect a large FPS drop. |
| **Anti-aliasing** | Off · FXAA · FXAA (extreme) | Edge smoothing applied to the final image. **Off ≈ +1.3 FPS**. *Extreme* is smoother and a little heavier than FXAA. |
| **Frame rate** | 60 FPS · 40 FPS · 30 FPS · Uncapped | 60 is the game's own target. **40** holds a steady 40 and switches the screen to 120 Hz, where every frame is shown for exactly the same time (needs a 120 Hz screen, like the Thor's). **30** caps at 30 for maximum steadiness and the lowest load. *Uncapped* is for testing only. |
| **Show FPS** | Off · On | Small FPS counter in the corner. No real cost. |
| **Shaders** | Async · Wait | *Wait*: everything is drawn correctly, with a brief stutter the first time a screen appears. *Async*: fewer stutters, but objects can pop in for a moment. |
| **GPU driver** | System · imported drivers | Turnip Gen8 V38 ≈ +2 FPS over older Turnip builds. Qualcomm's own drivers measured slower (~25 FPS) with graphical errors. |
| **Hidden passes** | Skipped · Drawn | Skips three rendering passes and the image copies between passes, none of which had a visible effect in testing (**≈ +4 FPS**). They were only checked in some venues and modes, so if anything looks wrong, set **Drawn** and compare, and please report it. |
| **Performance log** | Off · On | Records a performance profile for problem reports. Leave it off for normal play. |

Always on (not settings): single-sample rendering instead of the game's 4× MSAA (the biggest single
speed-up), a calmer wait for the graphics chip (the game no longer keeps a CPU core spinning, which
lowers CPU load and heat), a stereo sound mix made for the device's speakers (clearer hits), every game frame shown on
screen (earlier builds showed only about half of them), and a set of emulator fixes listed at the top of
[`stage2/native/patch_sdk_vulkan_fixes.py`](stage2/native/patch_sdk_vulkan_fixes.py).

## Controls

The game uses a controller, mapped like an Xbox 360 pad. Extra shortcuts while playing:

| Buttons | Action |
|---|---|
| **Back** (tap), or **Select + Start** | Return-to-start-screen pop-up (D-pad to choose, A to confirm, B to keep playing) |
| **L3 + R3** (press both sticks) | Screenshot, saved to `Pictures/TableTennis` with the date and time in the name |
| **Select** | The game's own camera button |

## Known issues

- Matches run at ~43–45 FPS rather than 60 on the AYN Thor (the graphics chip is the limit).
  The **40 FPS** setting gives a steady, even frame rate on a 120 Hz screen instead.
- Brief stutters the first time each screen loads (shader preparation; cached afterwards).
- *Hidden passes: Skipped* has only been checked in a few venues and modes. If something looks
  off, try *Drawn*.

## Reporting problems

Open an issue with:
1. what happened and where (venue, characters, mode), plus a screenshot (L3 + R3);
2. the log zip from the start screen: **Performance log → On**, reproduce, then **Export logs**.
   It contains logs and a device report only, no game files.

Please don't attach ISOs, APKs or other game files.

## Repository layout

| Path | What it is |
|---|---|
| `stage1/` | One-time setup: toolchain, ISO check, game recompilation |
| `stage2/` | Builds the APK: emulator fixes (`native/`), the Android app and launcher (`app/`) |
| `Install-Table-Tennis.cmd`, `tools/installer/` | Windows installer: your ISO in, APK out |
| `tools/use_version.sh` | Switch to an earlier build (`bash tools/use_version.sh 61`) |
| `assets/` | Images for this README (original artwork, no game assets) |
| `docs/BUILDING.md` | Detailed build steps, updating, troubleshooting |
| `HANDOFF.md` | Developer notes: every change, what it measured, current bottleneck |
| `NOTICE.md`, `LICENSE`, `licenses/` | Credits, license, and third-party license texts |

## Credits and license

Built on the work of:
- **[Table-Tennis-Rockstar-PC](https://github.com/davidptrssvea/Table-Tennis-Rockstar-PC)** by davidptrssvea: the PC static recompilation this port is based on (MIT).
- **[ReXGlue SDK](https://github.com/rexglue/rexglue-sdk)**: the recompiler and runtime (derived from the Xenia project).
- **[MCLA Android port](https://github.com/gabrielbskk/mcla-recomp-android-g)**: Android build layout and custom driver loading (GPL-3.0).
- **[libadrenotools](https://github.com/bylaws/libadrenotools)**, **SDL3**, and the **GamePocket** font (SIL OFL).

This repository is distributed under the **GPL-3.0** ([LICENSE](LICENSE)); details in [NOTICE.md](NOTICE.md).

*Rockstar Games presents Table Tennis* is a trademark of its respective owners. This is an
unofficial fan project for personal use, not affiliated with or endorsed by Rockstar Games,
Take-Two Interactive or Microsoft.
