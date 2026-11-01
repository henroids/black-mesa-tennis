package org.tabletennis.recomp;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.List;

/** Launcher settings, stored in SharedPreferences and turned into ReXGlue cvar arguments. */
final class Settings {
    static final String[] RESOLUTION_LABELS = {
            "720p (console native)",
            "1440p (2x, sharpest on the 1080p screen)"};
    static final String[] AA_LABELS = {"Off", "FXAA", "FXAA (extreme)"};
    static final String[] AA_VALUES = {"none", "fxaa", "fxaa_extreme"};
    static final String[] FPS_LABELS = {"60 FPS (console)", "30 FPS", "Uncapped"};
    static final int[] FPS_VALUES = {60, 30, 0};
    static final int[] SHADER_THREAD_VALUES = {2, 4, 6};

    // Visual-risk speed-ups (F10/F11: skip render passes with no visible effect so far). Kept
    // switchable so any venue / character / mode can be compared with the original quickly.
    static final String[] HIDDEN_PASS_LABELS = {"Skipped (faster)", "Drawn (original)"};
    int hiddenPasses = 0;

    // TEMPORARY test round (v70): remove the row when the round is done.
    static final String[] TEST_LABELS = {"Off", "Skip B group 1", "Skip B group 2",
            "Skip B group 3", "Skip B group 4", "Skip B group 5", "Skip B group 6"};
    int test = 0;

    private static final String PREFS = "tt_settings";

    int resolution = 0;
    int aa = 1;
    int fps = 0;
    boolean showFps = true;
    int shaderMode = 1;     // 0 = async (draws placeholders while compiling), 1 = wait (no missing objects)
    int shaderThreads = 1;  // index into SHADER_THREAD_VALUES
    boolean perfLog = true; // thread monitor + sampling profiler (logs/profile.txt)

    static Settings load(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        Settings s = new Settings();
        s.resolution = clamp(p.getInt("resolution", 0), RESOLUTION_LABELS.length);
        s.aa = clamp(p.getInt("aa", 1), AA_LABELS.length);
        s.fps = clamp(p.getInt("fps", 0), FPS_LABELS.length);
        s.showFps = p.getBoolean("show_fps", true);
        s.shaderMode = p.getInt("shader_mode", 1) == 0 ? 0 : 1;
        s.shaderThreads = clampDefault(p.getInt("shader_threads", 1), SHADER_THREAD_VALUES.length, 1);
        s.perfLog = p.getBoolean("perf_log", true);
        s.test = clamp(p.getInt("test_v70", 0), TEST_LABELS.length);
        s.hiddenPasses = clamp(p.getInt("hidden_passes", 0), HIDDEN_PASS_LABELS.length);
        return s;
    }

    void save(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt("resolution", resolution)
                .putInt("aa", aa)
                .putInt("fps", fps)
                .putBoolean("show_fps", showFps)
                .putInt("shader_mode", shaderMode)
                .putInt("shader_threads", shaderThreads)
                .putBoolean("perf_log", perfLog)
                .putInt("test_v70", test)
                .putInt("hidden_passes", hiddenPasses)
                .commit();
    }

    /** Every start-screen setting in plain words, for the logs. */
    String describe(String driver) {
        return "Resolution: " + RESOLUTION_LABELS[resolution]
                + " | Anti-aliasing: " + AA_LABELS[aa]
                + " | Frame rate: " + FPS_LABELS[fps]
                + " | Show FPS: " + (showFps ? "On" : "Off")
                + " | Shaders: " + (shaderMode == 0 ? "Async" : "Wait")
                + " | Shader threads: " + SHADER_THREAD_VALUES[shaderThreads]
                + " | GPU driver: " + driver
                + " | Test: " + TEST_LABELS[test]
                + " | Hidden passes: " + HIDDEN_PASS_LABELS[hiddenPasses]
                + " | Performance log: " + (perfLog ? "On" : "Off");
    }

    /** Command-line cvars for the native runtime. */
    List<String> toArguments(String isoPath) {
        List<String> args = new ArrayList<>();
        if (isoPath != null) args.add("--game_data_root=" + isoPath);
        int scale = resolution == 1 ? 2 : 1;
        // Guest video mode: what the game is told the TV is. Table Tennis is a 720p title.
        args.add("--video_mode_width=1280");
        args.add("--video_mode_height=720");
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
        // Present from the UI thread: otherwise the GPU command thread painted and waited
        // for the display's next refresh on every guest frame.
        args.add("--host_present_from_non_ui_thread=false");
        return args;
    }

    private static int clampDefault(int value, int count, int fallback) {
        return value < 0 || value >= count ? fallback : value;
    }

    private static int clamp(int value, int count) {
        return value < 0 || value >= count ? 0 : value;
    }
}
