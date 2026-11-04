#!/usr/bin/env bash
# Rockstar Table Tennis -> Android (AYN Thor) : Stage 2, build the installable APK.
# Run in the same WSL2 Ubuntu after Stage 1 succeeded:
#
#   bash ~/tt-android-kit2/tt_android_stage2.sh
#
# Output: TableTennis.apk, also copied to your Windows Downloads folder.
set -uo pipefail

WORK="${TT_WORK:-$HOME/tt-android}"
KIT2="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOGS="$WORK/logs2"
JOBS="${JOBS:-4}"
NDK_VER=30.0.16248370
export ANDROID_HOME="$WORK/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/$NDK_VER"
export REXSDK_DIR="$WORK/rexglue-sdk"
P="$WORK/project"
OUT="$WORK/out/android-arm64-release"
APP="$WORK/app"

mkdir -p "$LOGS"
exec > >(tee -a "$LOGS/00-console.log") 2>&1
step() { echo; echo "=== [$(date +%H:%M:%S)] $* ==="; }
package_logs() {
  rm -f "$WORK/tt-stage2-logs.zip"
  (cd "$WORK" && zip -qr tt-stage2-logs.zip logs2) 2>/dev/null
}
fail() {
  echo; echo "!!! FAILED: $*"
  package_logs
  echo "Send Claude: $WORK/tt-stage2-logs.zip"
  exit 1
}

# ---------------------------------------------------------------- 1
step "1/5 Checking Stage 1 results"
[ -f "$P/generated/default/sources.cmake" ] || fail "Stage 1 hasn't been run (no generated game code). Run Stage 1 first."
[ -f "$WORK/dist/assets/resource/flash/flash.rpf" ] || fail "Patched flash.rpf missing; re-run Stage 1."
[ -f "$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake" ] || fail "Android NDK missing; re-run Stage 1."
echo "  Stage 1 output found"

# ---------------------------------------------------------------- 2
step "2/5 Updating the native code (driver loader, fixes, profiler) and rebuilding"
cp "$KIT2/native/android_gpu_driver.cpp" "$P/src/android_gpu_driver.cpp"
cp "$KIT2/native/main.cpp" "$P/src/main.cpp"                   # sleep-based frame limiter on Android
cp "$KIT2/native/tabletennis_app.h" "$P/src/tabletennis_app.h" # no second limiter on the UI thread
cp "$KIT2/native/CMakeLists.txt" "$P/CMakeLists.txt"           # -Bsymbolic-functions for libmain
cp "$KIT2/native/tt_profiler.cpp" "$P/src/tt_profiler.cpp"     # built-in sampling profiler
python3 "$KIT2/native/patch_game_msaa.py" "$P/generated/default" > "$LOGS/02-game-edits.log" 2>&1 \
  || fail "game MSAA edit did not apply (logs2/02-game-edits.log)"
python3 "$KIT2/native/patch_game_gpu_wait.py" "$P/generated/default" >> "$LOGS/02-game-edits.log" 2>&1 \
  || fail "game GPU-wait edit failed (logs2/02-game-edits.log)"
sed 's/^/  /' "$LOGS/02-game-edits.log"
# SDK changes are applied to FRESH sources every build: a clean copy of the SDK (with only
# Stage 1's Android patches) is staged, today's patch_sdk_vulkan_fixes.py is applied to it,
# and only files whose content differs are copied into the SDK we build. Removing a change
# from the script really removes it, and unchanged files keep their timestamps so the
# rebuild stays incremental.
BASE="$WORK/rexglue-sdk-base"
STAGED="$WORK/rexglue-sdk-staged"
if [ ! -f "$BASE/.tt-base-ready" ]; then
  echo "  making a clean copy of the SDK sources (first time only, 1-2 min)"
  rm -rf "$BASE"
  git clone -q --branch v0.10.0 --depth 1 https://github.com/rexglue/rexglue-sdk "$BASE" \
    > "$LOGS/02-sdk-base.log" 2>&1 || fail "could not download clean SDK sources (logs2/02-sdk-base.log)"
  ( cd "$BASE" && git apply --include='src/*' --include='include/*' \
      "$WORK/mcla/patches/rexglue-v0.10.0-android-arm64.patch" ) >> "$LOGS/02-sdk-base.log" 2>&1 \
    || fail "Android SDK patch did not apply to the clean copy (logs2/02-sdk-base.log)"
  python3 "$WORK/mcla/scripts/patch_android_rexglue.py" --sdk "$BASE" >> "$LOGS/02-sdk-base.log" 2>&1 \
    || fail "patch_android_rexglue.py failed on the clean copy (logs2/02-sdk-base.log)"
  sed -i 's/WindowedApp::GetCreator("mcla")/WindowedApp::GetCreator("tabletennis")/' "$BASE/src/ui/windowed_app_main_sdl.cpp"
  sed -i 's#"mcla.log"#"tabletennis.log"#g; s#"mcla-guest-memory"#"tt-guest-memory"#g' \
    "$BASE/src/ui/rex_app.cpp" "$BASE/src/core/memory_posix.cpp"
  touch "$BASE/.tt-base-ready"
fi
rm -rf "$STAGED" && mkdir -p "$STAGED" && cp -a "$BASE/src" "$BASE/include" "$STAGED/"
python3 "$KIT2/native/patch_sdk_vulkan_fixes.py" "$STAGED" > "$LOGS/02-sdk-fixes.log" 2>&1 \
  || fail "SDK changes did not apply (logs2/02-sdk-fixes.log)"
sed 's/^/  /' "$LOGS/02-sdk-fixes.log"
rsync -rc "$STAGED/src/" "$REXSDK_DIR/src/" && rsync -rc "$STAGED/include/" "$REXSDK_DIR/include/" \
  || fail "could not update the SDK sources"
( cd "$P" && cmake --preset android-arm64-release ) > "$LOGS/02-configure.log" 2>&1 \
  || fail "CMake configure failed (logs2/02-configure.log)"
( cd "$P" && cmake --build --preset android-arm64-release --parallel "$JOBS" ) > "$LOGS/02-build.log" 2>&1 \
  || { grep -nE "error:|undefined|FAILED:" "$LOGS/02-build.log" | head -200 > "$LOGS/02-build-errors.txt"; fail "native build failed (logs2/02-build-errors.txt)"; }
echo "  native libraries up to date"

# ---------------------------------------------------------------- 3
step "3/5 Assembling the Android app project"
rm -rf "$APP"; mkdir -p "$APP/app/src/main/java/org" "$APP/app/jniLibs/arm64-v8a" "$APP/app/assets"
M="$WORK/mcla/android"
cp -r "$M/gradle" "$M/gradlew" "$M/build.gradle" "$APP/"
chmod +x "$APP/gradlew"
printf 'org.gradle.jvmargs=-Xmx2048m\nandroid.useAndroidX=false\n' > "$APP/gradle.properties"
echo "sdk.dir=$ANDROID_HOME" > "$APP/local.properties"
cp "$KIT2/app/settings.gradle" "$APP/settings.gradle"
cp "$KIT2/app/build.gradle" "$APP/app/build.gradle"
cp "$KIT2/app/AndroidManifest.xml" "$APP/app/src/main/AndroidManifest.xml"
cp -r "$M/app/src/main/java/org/libsdl" "$APP/app/src/main/java/org/"
cp -r "$KIT2/app/java/org/tabletennis" "$APP/app/src/main/java/org/"
mkdir -p "$APP/app/src/main/res"
cp -r "$M/app/src/main/res/values" "$APP/app/src/main/res/"
cp -r "$KIT2/app/res/." "$APP/app/src/main/res/"

# native libraries
J="$APP/app/jniLibs/arm64-v8a"
SDKOUT="$REXSDK_DIR/out/android-arm64"
cp "$OUT/libmain.so" "$SDKOUT/librexruntime.so" "$SDKOUT/librexgpu-xenos.so" "$J/" || fail "native libraries missing"
for h in main_hook hook_impl file_redirect_hook gsl_alloc_hook; do
  cp "$OUT/third_party/libadrenotools/src/hook/lib$h.so" "$J/" || fail "adrenotools hook lib$h.so missing"
done
LIBCXX=$(find "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt" -path '*aarch64-linux-android/libc++_shared.so' | head -1)
[ -n "$LIBCXX" ] || fail "libc++_shared.so not found in the NDK"
cp "$LIBCXX" "$J/"
cp "$WORK/dist/assets/resource/flash/flash.rpf" "$APP/app/assets/flash.rpf"
cp -r "$KIT2/app/assets/." "$APP/app/assets/"   # GamePocket pixel font (SIL OFL)
# symbol lists for the runtime libraries (SDK code only), so profiler offsets can be named
mkdir -p "$APP/app/assets/syms"
NM=$(find "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt" -name llvm-nm | head -1)
for lib in librexgpu-xenos.so librexruntime.so; do
  "$NM" -C --defined-only "$SDKOUT/$lib" 2>/dev/null | grep -iE " [tTwW] " > "$APP/app/assets/syms/${lib%.so}.syms.txt" || true
done

# every library a packaged .so needs must be packaged too, or be a system library
READELF=$(find "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt" -name llvm-readelf | head -1)
SYSTEM_LIBS="libc.so libm.so libdl.so liblog.so libandroid.so libvulkan.so libEGL.so libGLESv2.so libGLESv1_CM.so libOpenSLES.so libaaudio.so libmediandk.so libnativewindow.so libz.so libjnigraphics.so libsync.so libGLESv3.so libbinder_ndk.so libicu.so libOpenMAXAL.so libcamera2ndk.so"
missing=""
for so in "$J"/*.so; do
  for need in $("$READELF" -d "$so" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'); do
    [ -f "$J/$need" ] && continue
    case " $SYSTEM_LIBS " in *" $need "*) continue;; esac
    missing="$missing $(basename "$so")->$need"
  done
done
ls -la "$J" > "$LOGS/03-jnilibs.txt"
[ -z "$missing" ] || echo "  WARNING: libraries not packaged (may be system libs):$missing"
echo "  app project ready ($(ls "$J" | wc -l) native libraries, flash.rpf staged)"

# ---------------------------------------------------------------- 4
step "4/5 Building the APK with Gradle (first time downloads Gradle, ~5-10 min)"
( cd "$APP" && ./gradlew --no-daemon :app:assembleDebug ) > "$LOGS/04-gradle.log" 2>&1 \
  || { grep -nE "error:|FAILURE|What went wrong" -A4 "$LOGS/04-gradle.log" | head -120 > "$LOGS/04-gradle-errors.txt"; fail "Gradle build failed (logs2/04-gradle-errors.txt)"; }
APK="$APP/app/build/outputs/apk/debug/app-debug.apk"
[ -f "$APK" ] || fail "APK not produced"
cp "$APK" "$WORK/TableTennis.apk"
echo "  built $(du -h "$WORK/TableTennis.apk" | cut -f1) APK"

# ---------------------------------------------------------------- 5
step "5/5 Copying the APK to Windows"
WINPROFILE=$(cmd.exe /c "echo %USERPROFILE%" 2>/dev/null | tr -d '\r')
DEST=""
if [ -n "$WINPROFILE" ] && command -v wslpath >/dev/null; then
  DEST="$(wslpath "$WINPROFILE")/Downloads"
fi
package_logs
if [ -n "$DEST" ] && [ -d "$DEST" ] && cp "$WORK/TableTennis.apk" "$DEST/TableTennis.apk"; then
  echo "  copied to your Windows Downloads folder: TableTennis.apk"
else
  echo "  could not find your Downloads folder automatically; run:"
  echo "  cp ~/tt-android/TableTennis.apk /mnt/c/Users/YOURNAME/Downloads/"
fi
echo
echo "Stage 2 complete. Install TableTennis.apk on the Thor."
echo "(Logs, if needed: $WORK/tt-stage2-logs.zip)"
