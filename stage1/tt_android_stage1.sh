#!/usr/bin/env bash
# Rockstar Table Tennis -> Android (AYN Thor) : Stage 1 build kit
# Run inside WSL2 Ubuntu 24.04 on your Windows 11 PC.
#
#   bash tt_android_stage1.sh /mnt/c/path/to/TableTennis.iso
#
# Safe to re-run: finished steps are skipped. Everything lives in ~/tt-android.
# Your ISO is only read, never copied or modified. At the end, send Claude the
# file ~/tt-android/tt-stage1-logs.zip (logs only, no game data).
set -uo pipefail

ISO="${1:-}"
WORK="${TT_WORK:-$HOME/tt-android}"
JOBS="${JOBS:-$(( $(nproc) > 2 ? $(nproc) - 1 : 1 ))}"
LOGS="$WORK/logs"

PC_REPO=https://github.com/davidptrssvea/Table-Tennis-Rockstar-PC
PC_COMMIT=ee18b25eae16ed321aa9cd9e7a2eef7594fc825a
MCLA_REPO=https://github.com/gabrielbskk/mcla-recomp-android-g
MCLA_COMMIT=90d2f8fe53b1d5492f61249cf1c6060f5b57cfca
ADRENO_REPO=https://github.com/bylaws/libadrenotools
ADRENO_COMMIT=8fae8ce254dfc1344527e05301e43f37dea2df80
SDK_REPO=https://github.com/rexglue/rexglue-sdk
SDK_TAG=v0.10.0
HOST_SDK_URL=https://github.com/rexglue/rexglue-sdk/releases/download/v0.10.0/rexglue-sdk-0.10.0-linux-amd64.zip
HOST_SDK_SHA=0388edb3f8fb1444f1b0ad5d440cb02d9690ac20a76b04fd6b7dcc19f59328a6
CMDLINE_URL=https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
NDK_VER=30.0.16248370
ANDROID_PLATFORM_VER=35
BUILD_TOOLS_VER=35.0.0

KIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export ANDROID_HOME="$WORK/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/$NDK_VER"
export REXSDK_DIR="$WORK/rexglue-sdk"

mkdir -p "$WORK" "$LOGS"
exec > >(tee -a "$LOGS/00-console.log") 2>&1

step() { echo; echo "=== [$(date +%H:%M:%S)] $* ==="; }
fail() {
  echo; echo "!!! FAILED: $*"
  package_logs
  echo "Send Claude: $WORK/tt-stage1-logs.zip"
  exit 1
}
package_logs() {
  {
    echo "date: $(date -Is)"; uname -a; lsb_release -ds 2>/dev/null
    echo "cpus: $(nproc)"; free -h; df -h "$WORK"
    for t in clang clang++ cmake ninja python3 java git; do printf '%s: ' "$t"; "$t" --version 2>&1 | head -1; done
    for r in pc mcla rexglue-sdk; do [ -d "$WORK/$r/.git" ] && echo "$r @ $(git -C "$WORK/$r" rev-parse HEAD)"; done
    ls -la "$WORK/out/android-arm64-release/"*.so 2>/dev/null
  } > "$LOGS/99-environment.txt" 2>&1
  rm -f "$WORK/tt-stage1-logs.zip"
  (cd "$WORK" && zip -qr tt-stage1-logs.zip logs) || tar -czf "$WORK/tt-stage1-logs.tgz" -C "$WORK" logs
}
run_logged() { local log="$LOGS/$1"; shift; echo "  -> $* (log: logs/$(basename "$log"))"; "$@" > "$log" 2>&1; }

# ---------------------------------------------------------------- checks
step "1/9 Checking environment"
[ "$(uname -m)" = x86_64 ] || fail "This must run on x86_64 Linux (WSL2 Ubuntu)."
grep -qi microsoft /proc/version || echo "  (note: not WSL - fine if this is native Ubuntu)"
[ -n "$ISO" ] || fail "Usage: bash $0 /mnt/c/path/to/TableTennis.iso"
[ -f "$ISO" ] || fail "ISO not found: $ISO"
free_gb=$(df -BG --output=avail "$WORK" | tail -1 | tr -dc 0-9)
echo "  free disk in $WORK: ${free_gb} GB"; [ "$free_gb" -ge 40 ] || fail "Need at least 40 GB free (80 recommended)."
mem_gb=$(free -g | awk '/Mem:/{print $2}')
echo "  WSL memory: ${mem_gb} GB, parallel jobs: $JOBS"
[ "$mem_gb" -ge 12 ] || { JOBS=$(( JOBS > 4 ? 4 : JOBS )); echo "  low memory -> jobs limited to $JOBS"; }

# ---------------------------------------------------------------- packages
step "2/9 Installing Ubuntu packages (asks for your password once)"
PKGS="git curl unzip zip python3 cmake ninja-build clang lld openjdk-17-jdk-headless pkg-config patch rsync"
missing=""; for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  sudo apt-get update > "$LOGS/02-apt.log" 2>&1 && sudo apt-get install -y $missing >> "$LOGS/02-apt.log" 2>&1 \
    || fail "apt install failed (see logs/02-apt.log)"
fi
cmake_ver=$(cmake --version | head -1 | grep -oE '[0-9]+\.[0-9]+')
echo "  cmake $cmake_ver"
python3 - "$cmake_ver" <<'EOF' || fail "CMake 3.25+ is required (Ubuntu 24.04 has 3.28). Are you on Ubuntu 22.04?"
import sys; a,b=map(int,sys.argv[1].split('.')); sys.exit(0 if (a,b)>=(3,25) else 1)
EOF

# ---------------------------------------------------------------- android sdk/ndk
step "3/9 Android command-line tools, SDK and NDK $NDK_VER (~3 GB download)"
if [ ! -x "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" ]; then
  mkdir -p "$ANDROID_HOME/cmdline-tools"
  curl -fL --retry 3 -o "$WORK/cmdline-tools.zip" "$CMDLINE_URL" || fail "Could not download Android cmdline-tools"
  rm -rf "$ANDROID_HOME/cmdline-tools/latest" "$ANDROID_HOME/cmdline-tools/cmdline-tools"
  unzip -q "$WORK/cmdline-tools.zip" -d "$ANDROID_HOME/cmdline-tools" && mv "$ANDROID_HOME/cmdline-tools/cmdline-tools" "$ANDROID_HOME/cmdline-tools/latest"
fi
SDKM="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
if [ ! -f "$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" ]; then
  yes | "$SDKM" --licenses > "$LOGS/03-sdk-licenses.log" 2>&1
  run_logged 03-sdkmanager.log "$SDKM" --install "platform-tools" "platforms;android-$ANDROID_PLATFORM_VER" \
    "build-tools;$BUILD_TOOLS_VER" "ndk;$NDK_VER" \
    || fail "sdkmanager failed (see logs/03-sdkmanager.log)"
fi
[ -f "$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" ] || fail "NDK not installed at $ANDROID_NDK_HOME"
echo "  NDK ok: $ANDROID_NDK_HOME"

# ---------------------------------------------------------------- sources
step "4/9 Fetching sources (pinned versions)"
fetch() { # dir url commit
  if [ ! -d "$WORK/$1/.git" ]; then git clone -q "$2" "$WORK/$1" || return 1; fi
  git -C "$WORK/$1" fetch -q origin "$3" 2>/dev/null
  git -C "$WORK/$1" checkout -q "$3" || git -C "$WORK/$1" checkout -q FETCH_HEAD
}
fetch pc "$PC_REPO" "$PC_COMMIT" || fail "clone Table Tennis PC repo"
fetch mcla "$MCLA_REPO" "$MCLA_COMMIT" || fail "clone MCLA Android repo"
fetch libadrenotools "$ADRENO_REPO" "$ADRENO_COMMIT" || fail "clone libadrenotools"
git -C "$WORK/libadrenotools" submodule update -q --init --recursive || fail "libadrenotools submodules"
if [ ! -f "$REXSDK_DIR/.tt-android-patched" ]; then
  rm -rf "$REXSDK_DIR"
  git clone -q --branch "$SDK_TAG" --recurse-submodules --shallow-submodules "$SDK_REPO" "$REXSDK_DIR" \
    > "$LOGS/04-sdk-clone.log" 2>&1 || fail "clone ReXGlue SDK (see logs/04-sdk-clone.log)"
fi
echo "  SDK @ $(git -C "$REXSDK_DIR" rev-parse --short HEAD)"

# host-native codegen (prebuilt official linux-amd64 release)
HOST_SDK="$WORK/rexglue-host"
REXGLUE="$HOST_SDK/linux-amd64/bin/rexglue"
if [ ! -x "$REXGLUE" ]; then
  curl -fL --retry 3 -o "$WORK/rexglue-host.zip" "$HOST_SDK_URL" || fail "download host rexglue"
  echo "$HOST_SDK_SHA  $WORK/rexglue-host.zip" | sha256sum -c --quiet || fail "host rexglue checksum mismatch"
  rm -rf "$HOST_SDK"; unzip -q "$WORK/rexglue-host.zip" -d "$HOST_SDK"
fi
export LD_LIBRARY_PATH="$HOST_SDK/linux-amd64/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ "$("$REXGLUE" --version 2>&1)" = "0.10.0" ] || fail "host rexglue --version is not 0.10.0"
echo "  host codegen ok: rexglue 0.10.0"

# ---------------------------------------------------------------- patch sdk
step "5/9 Applying the Android patches to the SDK"
if [ ! -f "$REXSDK_DIR/.tt-android-patched" ]; then
  cp "$REXSDK_DIR/src/ui/rex_app.cpp" "$WORK/rex_app.stock.cpp"
  ( cd "$REXSDK_DIR" && git apply "$WORK/mcla/patches/rexglue-v0.10.0-android-arm64.patch" ) \
    > "$LOGS/05-sdk-patch.log" 2>&1 || fail "MCLA SDK patch did not apply (logs/05-sdk-patch.log)"
  python3 "$WORK/mcla/scripts/patch_android_rexglue.py" --sdk "$REXSDK_DIR" >> "$LOGS/05-sdk-patch.log" 2>&1 \
    || fail "patch_android_rexglue.py failed (logs/05-sdk-patch.log)"
  # The MCLA entry point looks up its app by name; ours registers as "tabletennis".
  sed -i 's/WindowedApp::GetCreator("mcla")/WindowedApp::GetCreator("tabletennis")/' "$REXSDK_DIR/src/ui/windowed_app_main_sdl.cpp"
  grep -q 'GetCreator("tabletennis")' "$REXSDK_DIR/src/ui/windowed_app_main_sdl.cpp" || fail "app-name patch"
  sed -i 's#"mcla.log"#"tabletennis.log"#g; s#"mcla-guest-memory"#"tt-guest-memory"#g' \
    "$REXSDK_DIR/src/ui/rex_app.cpp" "$REXSDK_DIR/src/core/memory_posix.cpp"
  diff -u "$WORK/rex_app.stock.cpp" "$REXSDK_DIR/src/ui/rex_app.cpp" > "$WORK/rex_app.android.diff"
  touch "$REXSDK_DIR/.tt-android-patched"
fi
echo "  SDK patched"

# ---------------------------------------------------------------- project
step "6/9 Assembling the Android project"
P="$WORK/project"
mkdir -p "$P"
rsync -a --exclude generated "$WORK/pc/project/" "$P/" 2>/dev/null || cp -r "$WORK/pc/project/." "$P/"
cp -r "$KIT_DIR/overlay/project/." "$P/"
cp "$WORK/mcla/src/android_gpu_driver.cpp" "$P/src/android_gpu_driver.cpp"
mkdir -p "$P/third_party"; rm -rf "$P/third_party/libadrenotools"
cp -r "$WORK/libadrenotools" "$P/third_party/libadrenotools"
# ISO-capable ReXApp + the Android hunks from the MCLA patch
cp "$P/sdk-overrides/rex_app.cpp" "$P/sdk-overrides/rex_app_android.cpp"
patch --no-backup-if-mismatch "$P/sdk-overrides/rex_app_android.cpp" < "$WORK/rex_app.android.diff" \
  > "$LOGS/06-rexapp-merge.log" 2>&1 || fail "rex_app Android merge (logs/06-rexapp-merge.log)"
echo "  project ready at $P"

# ---------------------------------------------------------------- game files
step "7/9 Validating your ISO and extracting default.xex + flash.rpf"
mkdir -p "$WORK/private_work" "$WORK/dist"
( cd "$P/tools" && python3 - "$ISO" "$WORK" ) > "$LOGS/07-iso.log" 2>&1 <<'EOF' || fail "ISO check/extract failed (logs/07-iso.log)"
import sys
from pathlib import Path
from disc_image import validate, extract
from patch_menu import patch as patch_menu
iso, root = Path(sys.argv[1]), Path(sys.argv[2])
validate(iso)
extract(iso, root)
patch_menu(root / 'dist/assets/resource/flash/flash.rpf')
print("ISO OK, files extracted, menu patched")
EOF
cat "$LOGS/07-iso.log" | tail -3

# ---------------------------------------------------------------- codegen
step "8/9 Recompiling the game code (codegen + 17 native edits)"
# manifest's game_root/file_path are ../private_work relative to project/
if [ ! -f "$P/generated/default/sources.cmake" ] || [ ! -f "$P/generated/.tt-native-patched" ]; then
  rm -rf "$P/generated/default"
  ( cd "$P" && "$REXGLUE" codegen "$P/tabletennis_manifest.toml" ) > "$LOGS/08-codegen.log" 2>&1 \
    || fail "codegen failed (logs/08-codegen.log)"
  ( cd "$P/tools" && python3 -c "import sys; from pathlib import Path; from patch_native import patch; patch(Path(sys.argv[1]))" "$P/generated/default" ) \
    > "$LOGS/08-native-edits.log" 2>&1 || fail "patch_native failed (logs/08-native-edits.log)"
  touch "$P/generated/.tt-native-patched"
fi
echo "  generated $(ls "$P/generated/default" | wc -l) files"

# ---------------------------------------------------------------- build
step "9/9 Building the Android native libraries (this is the long one: 30-90 min)"
( cd "$P" && cmake --preset android-arm64-release ) > "$LOGS/09-configure.log" 2>&1 \
  || fail "CMake configure failed (logs/09-configure.log)"
( cd "$P" && cmake --build --preset android-arm64-release --parallel "$JOBS" -- -k 0 ) > "$LOGS/09-build.log" 2>&1
build_rc=$?
grep -nE "error:|undefined reference|FAILED:" "$LOGS/09-build.log" | head -300 > "$LOGS/09-build-errors.txt"
package_logs
if [ $build_rc -ne 0 ]; then
  echo "Build had errors ($(grep -c 'error:' "$LOGS/09-build.log") lines). That's expected on the first try."
  echo "Send Claude: $WORK/tt-stage1-logs.zip"
  exit 1
fi
echo; echo "Stage 1 complete: native libraries built:"; ls -la "$WORK/out/android-arm64-release/"*.so
echo "Send Claude: $WORK/tt-stage1-logs.zip"
