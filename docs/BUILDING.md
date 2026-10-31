# Building black-mesa-tennis

This guide turns **your own** retail Xbox 360 ISO of *Rockstar Games presents Table Tennis*
into an APK for **your own** Android device. Nothing in this repository contains game code
or data; everything game-related is produced on your computer from your ISO, and the
resulting APK must not be shared.

> **On Windows, the easiest route is `Install-Table-Tennis.cmd`** in the repository root: it does
> everything below for you (see the README). This page is the manual route and the reference.

## 1. Prepare the build computer

### Windows 10/11 (WSL2)
1. Open **PowerShell as Administrator** and run `wsl --install -d Ubuntu-24.04`.
2. Reboot. Ubuntu opens and asks for a user name and password (you'll need the password once
   during stage 1).
3. Run every command below in the **Ubuntu** window, not in PowerShell. (In PowerShell, type
   `wsl` first to switch to Ubuntu.)

Windows drives appear under `/mnt/`: `C:\Games\TableTennis.iso` becomes
`/mnt/c/Games/TableTennis.iso`.

### Ubuntu 24.04 (native)
Works the same; the APK just isn't copied to a Windows Downloads folder automatically. It
stays at `~/tt-android/TableTennis.apk`.

## 2. Get the repository

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/henroids/black-mesa-tennis ~/black-mesa-tennis
```

## 3. Stage 1 (once)

```bash
bash ~/black-mesa-tennis/stage1/tt_android_stage1.sh "/mnt/c/Games/TableTennis.iso"
```

What it does, in order: installs build tools (asks for your password once), downloads the
Android SDK/NDK and the pinned open-source projects, **checks your ISO** (USA/Europe retail,
Title ID `545407DF`; it stops early if the ISO isn't supported), extracts the files the
recompiler needs from it, recompiles the game code and builds the native libraries.
It takes 1–2 hours. Re-running it is safe; finished steps are skipped.

Everything it creates lives in `~/tt-android/`. Your ISO is only read.

## 4. Stage 2 (each update)

```bash
cd ~/black-mesa-tennis && git pull && bash stage2/tt_android_stage2.sh
```

It applies this project's emulator changes to a fresh copy of the ReXGlue SDK sources
(so removed changes are really gone), rebuilds what changed, packages the Android app and
copies `TableTennis.apk` to your Windows **Downloads** folder. The first stage 2 run downloads
a clean copy of the SDK sources once.

## 5. Install

Copy the APK to the device and install it, then follow
[Installing and first launch](../README.md#installing-and-first-launch).

## Going back to an earlier build

```bash
cd ~/black-mesa-tennis && bash tools/use_version.sh 61   # any build number from v56 on
bash stage2/tt_android_stage2.sh
git checkout main && git pull                            # back to the newest build later
```

## Troubleshooting

| Problem | What to do |
|---|---|
| `wsl` / `bash` commands do nothing in PowerShell | Open the **Ubuntu** app (or type `wsl` first). |
| Stage 1 stops at the ISO check | The ISO isn't the supported USA/Europe retail release, or the file is damaged. Make a fresh image of your disc. |
| `ISO not found` | Check the path: forward slashes, `/mnt/c/...`, and keep the quotes if it has spaces. |
| A stage fails | It prints `FAILED:` and the log file. Open an issue and attach `~/tt-android/tt-stage1-logs.zip` or `tt-stage2-logs.zip` (logs only, no game files). |
| The game starts with the system driver after a crash | The launcher falls back to the system driver if the last session with a custom driver didn't close cleanly. Select your driver again. |
| Something looks wrong in game | Start screen → **Hidden passes → Drawn** and compare; then report it with a screenshot (L3 + R3) and the exported logs. |
