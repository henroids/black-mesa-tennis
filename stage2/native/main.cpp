// tabletennis - ReXGlue Recompiled Project

#include "generated/default/tabletennis_init.h"
#include <rex/cvar.h>
#include <chrono>
#include <thread>
#include <atomic>
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
// here (see patch_game_msaa.py). TT_GAME_MSAA=off makes it render with 1 sample, which
// fits the frame in a single EDRAM tile instead of drawing the scene once per strip.
extern "C" uint32_t TtGameMsaaOverride(uint32_t samples) {
  static const bool off = [] {
    const char* v = std::getenv("TT_GAME_MSAA");
    return v && std::string_view(v) == "off";
  }();
  const uint32_t result = off && samples > 1 ? 1 : samples;
  static std::atomic<uint32_t> last{0xFFFFFFFFu};
  if (last.exchange(samples) != samples) {
    REXLOG_INFO("tt: game tiled rendering requested {} samples, using {}", samples, result);
  }
  return result;
}

std::atomic<float> g_tt_game_fps{0.0f};
std::atomic<float> g_tt_game_worst_ms{0.0f};

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
      REXLOG_INFO("GAME FPS: {:.1f} (avg {:.1f} ms, worst {:.1f} ms)", frames / elapsed,
                  elapsed * 1000.0f / frames, worst_ms);
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
    if (!std::getenv("TT_STRICT_FETCH")) rex::cvar::SetFlagByName("gpu_allow_invalid_fetch_constants", "true");
  }
} g_early_init;
}  // namespace

REX_DEFINE_APP(tabletennis, TabletennisApp::Create)
