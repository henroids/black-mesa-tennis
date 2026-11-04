// tabletennis - ReXGlue Recompiled Project

#include "generated/default/tabletennis_init.h"
#include <rex/cvar.h>
#include <chrono>
#include <thread>
#include <atomic>
#include <cstdio>
#include <string_view>
#if defined(__ANDROID__)
#include <dlfcn.h>
#endif
#include <cstdlib>
#if defined(_M_X64) || defined(__x86_64__)
#include <immintrin.h>
#endif

#include "tabletennis_app.h"

REXCVAR_DEFINE_BOOL(show_fps, false, "UI", "Show in-game FPS counter");
REXCVAR_DEFINE_INT32(framerate, 60, "Video", "Target framerate limit (60, 120, 144, 240, 0=unlimited)");

#undef __imp__VdSwap
extern "C" REX_FUNC(__imp__VdSwap);


// tt-android: the game's tiled-rendering setup passes its MSAA sample count through
// here (see patch_game_msaa.py). Rendering with 1 sample fits the frame in a single
// EDRAM tile instead of drawing the scene once per strip (~20 -> ~29 FPS when added;
// host FXAA still smooths edges).
// The game's wait-for-GPU loop (sub_82359DC0, see patch_game_gpu_wait.py) spins on a CPU
// core while the GPU is busy, which is all match long. On the first pass of each spin
// countdown, sleep 100 us instead (the GPU check is unchanged). v76: that thread went from
// ~86% to ~21% of a big core, same FPS.
extern "C" void TtGpuWaitPause(uint32_t countdown) {
  if (countdown == 100) std::this_thread::sleep_for(std::chrono::microseconds(100));
}

extern "C" uint32_t TtGameMsaaOverride(uint32_t samples) {
  const uint32_t result = 1;
  static std::atomic<uint32_t> last{0xFFFFFFFFu};
  if (last.exchange(samples) != samples) {
    REXLOG_INFO("tt: game tiled rendering requested {} samples, using {}", samples, result);
  }
  return result;
}

std::atomic<float> g_tt_game_fps{0.0f};
std::atomic<float> g_tt_game_worst_ms{0.0f};

// tt-android: frame-time statistics for the logs (read by the profiler via dlsym).
// [0..4] frames that took <=18 ms (~60 FPS), <=24 (~45), <=35 (~30), <=55 (~20), longer
// [5..9] microseconds spent in those frames   [10] spikes >= 80 ms   [11] total frames
extern "C" __attribute__((visibility("default"))) uint64_t tt_frame_hist[16] = {};
static std::chrono::steady_clock::time_point TtSessionStart() {
  static const auto start = std::chrono::steady_clock::now();
  return start;
}
static void TtSessionTime(char* out, size_t size) {
  const long long s = std::chrono::duration_cast<std::chrono::seconds>(
                          std::chrono::steady_clock::now() - TtSessionStart()).count();
  std::snprintf(out, size, "%lld:%02lld", s / 60, s % 60);
}
static void TtRecordFrame(float frame_ms) {
  TtSessionStart();
  const int b = frame_ms <= 18.f ? 0 : frame_ms <= 24.f ? 1 : frame_ms <= 35.f ? 2 : frame_ms <= 55.f ? 3 : 4;
  __atomic_fetch_add(&tt_frame_hist[b], 1, __ATOMIC_RELAXED);
  __atomic_fetch_add(&tt_frame_hist[5 + b], uint64_t(frame_ms * 1000.f), __ATOMIC_RELAXED);
  __atomic_fetch_add(&tt_frame_hist[11], 1, __ATOMIC_RELAXED);
  if (frame_ms >= 80.f) {
    __atomic_fetch_add(&tt_frame_hist[10], 1, __ATOMIC_RELAXED);
    char when[16];
    TtSessionTime(when, sizeof when);
    REXLOG_INFO("tt: frame spike {:.0f} ms at {} into the session", frame_ms, when);
  }
}

extern "C" void Tabletennis_VdSwap_Hook(PPCContext& ctx, uint8_t* base) {
  // Call original VdSwap to commit guest frame to GPU
  __imp__VdSwap(ctx, base);


  // Real game frame rate: one VdSwap per guest frame (the overlay's ImGui rate only
  // measures how often the host UI redraws).
  {
    using clock = std::chrono::steady_clock;
    static auto window_start = clock::now();
    static auto previous = clock::now();
    static int frames = 0;
    static float worst_ms = 0.0f;
    auto now = clock::now();
    float frame_ms = std::chrono::duration<float, std::milli>(now - previous).count();
    previous = now;
    if (frame_ms > worst_ms) worst_ms = frame_ms;
    TtRecordFrame(frame_ms);
    ++frames;
#if defined(__ANDROID__)
    // After ~5 s of real frames the GPU driver has clearly started fine: clear the
    // launcher's "custom driver crashed" marker so the driver choice is kept.
    static int total_frames = 0;
    if (++total_frames == 300) {
      if (auto* running = reinterpret_cast<void (*)()>(dlsym(RTLD_DEFAULT, "TtCustomDriverRunning"))) {
        running();
      }
    }
#endif
    float elapsed = std::chrono::duration<float>(now - window_start).count();
    if (elapsed >= 2.0f) {
      g_tt_game_fps = frames / elapsed;
      g_tt_game_worst_ms = worst_ms;
      char when[16];
      TtSessionTime(when, sizeof when);
      const float window_fps = frames / elapsed;
      REXLOG_INFO("GAME FPS: {:.1f} (avg {:.1f} ms, worst {:.1f} ms) [session {}]", window_fps,
                  elapsed * 1000.0f / frames, worst_ms, when);
      // Flag sudden drops (25% or more below the previous 2-second window).
      static float previous_fps = 0.0f;
      if (previous_fps > 0.0f && window_fps < previous_fps * 0.75f) {
        REXLOG_INFO("tt: FPS drop {:.0f} -> {:.0f} at {} into the session", previous_fps,
                    window_fps, when);
      }
      previous_fps = window_fps;
      frames = 0;
      worst_ms = 0.0f;
      window_start = now;
    }
  }

  // High-precision frame limiter for high refresh rate modes
  int32_t target_fps = REXCVAR_GET(framerate);
  if (target_fps > 0) {
    using clock = std::chrono::steady_clock;
    static auto last_swap = clock::now();
    const auto target_duration = std::chrono::nanoseconds(1'000'000'000LL / target_fps);

    auto now = clock::now();
    auto elapsed = now - last_swap;
    if (elapsed < target_duration) {
#if defined(__ANDROID__)
      // Phones: sleep instead of spinning. A spin loop pins a big core at 100%
      // (ARM has no _mm_pause here), steals time from the game threads and heats
      // the SoC into throttling. Sleep granularity on Android is well under 1 ms.
      std::this_thread::sleep_until(last_swap + target_duration);
#else
      auto sleep_dur = target_duration - elapsed;
      if (sleep_dur > std::chrono::milliseconds(2)) {
        std::this_thread::sleep_for(sleep_dur - std::chrono::milliseconds(1));
      }
      while (clock::now() - last_swap < target_duration) {
#if defined(_M_X64) || defined(__x86_64__)
        _mm_pause();
#endif
      }
#endif
    }
    last_swap = clock::now();
  }
}

extern "C" void __imp__Tabletennis_VdSwap_Hook(PPCContext& ctx, uint8_t* base) {
  Tabletennis_VdSwap_Hook(ctx, base);
}

// Returns SignedInLocally for user 0, allowing offline profiles/tournaments, but NOT Xbox Live.
extern "C" void Tabletennis_XamUserGetSigninState_Hook(PPCContext& ctx, uint8_t* base) {
  uint32_t user_index = ctx.r3.u32;
  if (user_index == 0) {
    ctx.r3.u64 = 1; // eXUserSigninState_SignedInLocally
  } else {
    ctx.r3.u64 = 0; // eXUserSigninState_NotSignedIn
  }
}

extern "C" void __imp__Tabletennis_XamUserGetSigninState_Hook(PPCContext& ctx, uint8_t* base) {
  Tabletennis_XamUserGetSigninState_Hook(ctx, base);
}

// Denies Xbox Live multiplayer privileges (XPRIVILEGE_MULTIPLAYER_SESSIONS),
// cleanly graying out and disabling the "Xbox Live" option in the main menu.
extern "C" void Tabletennis_XamUserCheckPrivilege_Hook(PPCContext& ctx, uint8_t* base) {
  uint32_t result_ptr = ctx.r5.u32;
  if (result_ptr != 0) {
    REX_STORE_U32(result_ptr, 0); // 0 = Denied / Disabled
  }
  ctx.r3.u64 = 0;
}

extern "C" void __imp__Tabletennis_XamUserCheckPrivilege_Hook(PPCContext& ctx, uint8_t* base) {
  Tabletennis_XamUserCheckPrivilege_Hook(ctx, base);
}

// Blocks any attempts to open the sign-in / Xbox Live UI, preventing infinite hangs.
extern "C" void Tabletennis_XamShowSigninUI_Hook(PPCContext& ctx, uint8_t* base) {
  REXLOG_INFO("Tabletennis: Blocked XamShowSigninUI (Xbox Live is disabled in offline PC port).");
  ctx.r3.u64 = 1223; // ERROR_CANCELLED
}

extern "C" void __imp__Tabletennis_XamShowSigninUI_Hook(PPCContext& ctx, uint8_t* base) {
  Tabletennis_XamShowSigninUI_Hook(ctx, base);
}

namespace {
struct EarlyInit {
  EarlyInit() {
    rex::cvar::SetFlagByName("protect_zero", "false");
    rex::cvar::SetFlagByName("gpu_allow_invalid_fetch_constants", "true");
  }
} g_early_init;
}  // namespace

REX_DEFINE_APP(tabletennis, TabletennisApp::Create)
