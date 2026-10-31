package org.tabletennis.recomp;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.List;

/** Launcher settings, stored in SharedPreferences and turned into ReXGlue cvar arguments. */
final class Settings {
    // Resolution: index into RESOLUTION_* arrays.
    static final String[] RESOLUTION_LABELS = {
            "720p (console native)",
            "1440p (2x, sharpest on the 1080p screen)",
            "540p (performance, experimental)"};
    static final String[] AA_LABELS = {"Off", "FXAA", "FXAA (extreme)"};
    static final String[] AA_VALUES = {"none", "fxaa", "fxaa_extreme"};
    static final String[] FPS_LABELS = {"60 FPS (console)", "30 FPS", "Uncapped",
            "60 FPS, 30 Hz screen (test)", "60 FPS, 20 Hz screen (test)"};
    static final int[] FPS_VALUES = {60, 30, 0, 60, 60};
    /** Screen (UI/presenter) redraw rate for the experimental options, or null for 60 Hz. */
    String uiHz() { return fps == 3 ? "30" : fps == 4 ? "20" : null; }
    static final String[] TURNIP_LABELS = {"Auto", "Sysmem (TU_DEBUG=sysmem)", "Sysmem + no binning"};
    static final String[] TURNIP_VALUES = {"", "sysmem", "sysmem,nobin"};

    private static final String PREFS = "tt_settings";

    int resolution = 0;
    int aa = 1;
    int fps = 0;
    boolean showFps = true;
    int turnipMode = 0;
    int shaderMode = 1;     // 0 = async (draws placeholders while compiling), 1 = wait (no missing objects)
    int shaderThreads = 1;  // index into SHADER_THREAD_VALUES
    static final int[] SHADER_THREAD_VALUES = {2, 4, 6};
    // Renderer experiments for the black skin/hair: each sets one or two GPU cvars.
    static final String[] EXPERIMENT_LABELS = {"None", "A: Resolve readback", "B: Snorm16 RT off",
            "C: Gamma RT as sRGB", "D: No 2x MSAA", "E: Occlusion off", "F: No 3D-to-2D tex",
            "G: B + C + D + E", "H: Strict fetch constants"};
    static final String[][] EXPERIMENT_ARGS = {
            {},
            {"--vulkan_readback_resolve=true"},
            {"--snorm16_render_target_full_range=false"},
            {"--gamma_render_target_as_unorm16=false"},
            {"--native_2x_msaa=false"},
            {"--occlusion_query_enable=false"},
            {"--gpu_3d_to_2d_texture=false"},
            {"--snorm16_render_target_full_range=false", "--gamma_render_target_as_unorm16=false",
             "--native_2x_msaa=false", "--occlusion_query_enable=false"},
            {"--gpu_allow_invalid_fetch_constants=false"},  // + TT_STRICT_FETCH env (GameActivity)
    };
    static final int EXPERIMENT_STRICT_FETCH = 8;
    boolean dumpShaders = false;
    // Occlusion queries: 0 = accurate (every query waits for the GPU), 1 = fast (no waits,
    // the game is told everything is visible).
    static final String[] OCCLUSION_LABELS = {"Accurate", "Fast"};
    int occlusion = 0;
    // Frame pacing experiments (the game sits at exactly 20 FPS = 60 / 3).
    static final String[] PACING_LABELS = {"Off", "A: 6 frames in flight", "B: 4 frames in flight",
            "C: UI not capped", "D: Present on UI thread", "E: A + C"};
    int pacing = 0;
    // Game MSAA: off = the game renders 1 sample in one EDRAM tile instead of drawing
    // the scene once per strip (the big GPU cost). Host FXAA still smooths edges.
    static final String[] GAME_MSAA_LABELS = {"Off (faster)", "On (as shipped)"};
    int gameMsaa = 0;
    // Frames the emulator may queue on the GPU before waiting (v44 test row).
    static final String[] QUEUED_LABELS = {"3 (default)", "4", "6"};
    int queued = 0;
    // v50 measurement: skip buffer/memory-only GPU syncs (may glitch the picture).
    static final String[] SYNC_TEST_LABELS = {"Off", "Skip buffer syncs (test)", "Skip texture reloads (test)"};
    int syncTest = 0;
    String framesInFlight() { return queued == 1 ? "4" : queued == 2 ? "6" : null; }
    boolean uiUncapped() { return pacing == 3 || pacing == 5; }
    boolean fastWatch = true;   // skip the /proc/self/maps recheck on write-watch faults (#441)
    int experiment = 0;
    boolean perfLog = true;     // thread monitor + sampling profiler (logs/profile.txt)
    String perfLogPath;
    String shaderDumpPath;         // set by the caller (needs a Context)

    static Settings load(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        Settings s = new Settings();
        s.resolution = clamp(p.getInt("resolution", 0), RESOLUTION_LABELS.length);
        s.aa = clamp(p.getInt("aa", 1), AA_LABELS.length);
        s.fps = clamp(p.getInt("fps", 0), FPS_LABELS.length);
        s.showFps = p.getBoolean("show_fps", true);
        s.turnipMode = clamp(p.getInt("turnip_mode", 0), TURNIP_LABELS.length);
        s.shaderMode = p.getInt("shader_mode", 1) == 0 ? 0 : 1;
        s.perfLog = p.getBoolean("perf_log", true);
        s.experiment = clamp(p.getInt("experiment", 0), EXPERIMENT_LABELS.length);
        s.dumpShaders = false;  // menu row retired (skin fixed); always off
        s.fastWatch = p.getBoolean("fast_watch", true);
        s.occlusion = clamp(p.getInt("occlusion", 0), OCCLUSION_LABELS.length);
        s.pacing = clamp(p.getInt("pacing", 0), PACING_LABELS.length);
        s.gameMsaa = clamp(p.getInt("game_msaa", 0), GAME_MSAA_LABELS.length);
        s.queued = clamp(p.getInt("frames_queued", 0), QUEUED_LABELS.length);
        s.syncTest = clamp(p.getInt("sync_test", 0), SYNC_TEST_LABELS.length);
        s.shaderDumpPath = new java.io.File(GameFiles.logsDir(context), "shaders").getAbsolutePath();
        s.perfLogPath = new java.io.File(GameFiles.logsDir(context), "perf.csv").getAbsolutePath();
        s.shaderThreads = clampDefault(p.getInt("shader_threads", 1), SHADER_THREAD_VALUES.length, 1);
        return s;
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt("resolution", resolution)
                .putInt("aa", aa)
                .putInt("fps", fps)
                .putBoolean("show_fps", showFps)
                .putInt("turnip_mode", turnipMode)
                .putInt("shader_mode", shaderMode)
                .putBoolean("perf_log", perfLog)
                .putInt("experiment", experiment)
                .putBoolean("dump_shaders", dumpShaders)
                .putBoolean("fast_watch", fastWatch)
                .putInt("occlusion", occlusion)
                .putInt("pacing", pacing)
                .putInt("game_msaa", gameMsaa)
                .putInt("frames_queued", queued)
                .putInt("sync_test", syncTest)
                .putInt("shader_threads", shaderThreads)
                .commit();
    }

    String turnipDebug() { return TURNIP_VALUES[turnipMode]; }

    /** Every start-screen setting in plain words, for the logs. */
    String describe(String driver) {
        return "Resolution: " + RESOLUTION_LABELS[resolution]
                + " | Anti-aliasing: " + AA_LABELS[aa]
                + " | Frame rate: " + FPS_LABELS[fps]
                + " | Show FPS: " + (showFps ? "On" : "Off")
                + " | Shaders: " + (shaderMode == 0 ? "Async" : "Wait")
                + " | Shader threads: " + SHADER_THREAD_VALUES[shaderThreads]
                + " | GPU driver: " + driver
                + " | Turnip mode: " + TURNIP_LABELS[turnipMode]
                + " | Game MSAA: " + GAME_MSAA_LABELS[gameMsaa]
                + " | Frames queued: " + QUEUED_LABELS[queued]
                + " | Sync test: " + SYNC_TEST_LABELS[syncTest]
                + " | Fast memory watch: " + (fastWatch ? "On" : "Off")
                + " | Performance log: " + (perfLog ? "On" : "Off");
    }

    /** Command-line cvars for the native runtime. */
    List<String> toArguments(String isoPath) {
        List<String> args = new ArrayList<>();
        if (isoPath != null) args.add("--game_data_root=" + isoPath);
        int width = 1280, height = 720, scale = 1;
        if (resolution == 1) {
            scale = 2;
        } else if (resolution == 2) {
            width = 960;
            height = 540;
        }
        // Guest video mode: what the game is told the TV is. Table Tennis is a 720p title.
        args.add("--video_mode_width=" + width);
        args.add("--video_mode_height=" + height);
        // Host-side internal render scale (1 = native, 2 = 2x each axis).
        args.add("--resolution_scale=" + scale);
        args.add("--draw_resolution_scale_x=" + scale);
        args.add("--draw_resolution_scale_y=" + scale);
        args.add("--swap_post_effect=" + AA_VALUES[aa]);
        args.add("--framerate=" + FPS_VALUES[fps]);
        args.add("--show_fps=" + showFps);
        args.add("--fullscreen=true");
        args.add("--async_shader_compilation=" + (shaderMode == 0));
        args.add("--vulkan_pipeline_creation_threads=" + SHADER_THREAD_VALUES[shaderThreads]);
        args.add("--protect_zero=false");
        args.add("--gpu_allow_invalid_fetch_constants=true");
        if (occlusion == 1) args.add("--occlusion_query_enable=false");
        // Present from the UI thread. With the default (present from the guest-output thread)
        // the GPU command thread itself painted and waited for a free swapchain image, i.e.
        // for the display's next refresh, on every guest frame (v31 profile: ~50% of it).
        args.add("--host_present_from_non_ui_thread=false");
        for (String extra : EXPERIMENT_ARGS[experiment]) args.add(extra);
        if (dumpShaders && shaderDumpPath != null) args.add("--dump_shaders=" + shaderDumpPath);
        return args;
    }

    private static int clampDefault(int value, int count, int fallback) {
        return value < 0 || value >= count ? fallback : value;
    }

    private static int clamp(int value, int count) {
        return value < 0 || value >= count ? 0 : value;
    }
}
